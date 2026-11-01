"""Structured shader graphs, preset rendering and PBR delivery without Python tools."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import struct
import zlib

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from mcpblender.config import Config
from mcpblender.server import build_server
from mcpblender.shader_contracts import ShaderNode
from test_asset_editing import assets_live, client_for, tool, pytestmark


def png(path, pixel):
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(16)) for y in range(16))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 16, 16, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def test_shader_graph_preflight_editing_and_rendered_pbr_export(assets_live):
    root, path = assets_live
    library = root / "library"
    library.mkdir(exist_ok=True)
    png(library / "paint.png", lambda x, y: (235, 90, 25) if (x // 4 + y // 4) % 2 else (30, 100, 180))
    png(library / "rough.png", lambda x, y: (100, 100, 100))
    png(library / "metal.png", lambda x, y: (150, 150, 150))
    png(library / "normal.png", lambda x, y: (128, 128, 255))

    async def run():
        async with client_for(path) as client:
            assert "execute_code" not in {t.name for t in await client.list_tools()}
            catalog = await tool(client, "shader_node_catalog", node_type="ShaderNodeMath")
            assert "MULTIPLY" in catalog["editable_properties"]["operation"]["choices"]
            graph = {"material": "Procedural", "nodes": [
                {"name": "Surface", "type": "ShaderNodeBsdfPrincipled"},
                {"name": "Output", "type": "ShaderNodeOutputMaterial"},
                {"name": "Noise", "type": "ShaderNodeTexNoise", "inputs": [{"socket": "Scale", "value": 4}]},
                {"name": "Ramp", "type": "ShaderNodeValToRGB", "ramp": [
                    {"position": 0, "color": [.02, .05, .2, 1]}, {"position": 1, "color": [.8, .2, .01, 1]}]}],
                "links": [{"from_node": "Noise", "from_socket": "Fac", "to_node": "Ramp", "to_socket": "Fac"},
                          {"from_node": "Ramp", "from_socket": "Color", "to_node": "Surface", "to_socket": "Base Color"},
                          {"from_node": "Surface", "from_socket": "BSDF", "to_node": "Output", "to_socket": "Surface"}]}
            created = await tool(client, "configure_shader_graph", **graph)
            assert len(created["nodes"]) == 4 and len(created["links"]) == 3
            with pytest.raises(ToolError, match="replace=true"):
                await tool(client, "configure_shader_graph", **graph)
            before = await tool(client, "inspect_shader_graph", material="Procedural")
            for mutation in (
                {"links": [{"from_node": "Noise", "from_socket": "Missing", "to_node": "Surface", "to_socket": "Roughness"}]},
                {"links": [{"from_node": "Surface", "from_socket": "BSDF", "to_node": "Surface", "to_socket": "Base Color"}]},
            ):
                with pytest.raises(ToolError):
                    await tool(client, "configure_shader_graph", **(graph | mutation | {"replace": True}))
                after = await tool(client, "inspect_shader_graph", material="Procedural")
                assert {k: v for k, v in after.items() if k != "operation_id"} == {k: v for k, v in before.items() if k != "operation_id"}
            await tool(client, "configure_shader_node", material="Procedural", node={"name": "Math", "type": "ShaderNodeMath", "properties": {"operation": "MULTIPLY"}, "inputs": [{"socket": 1, "value": .3}]})
            link = {"from_node": "Noise", "from_socket": "Fac", "to_node": "Math", "to_socket": 0}
            await tool(client, "connect_shader_nodes", material="Procedural", links=[link])
            with pytest.raises(ToolError, match="already linked"):
                await tool(client, "connect_shader_nodes", material="Procedural", links=[link])
            await tool(client, "disconnect_shader_input", material="Procedural", node="Math", socket=0)
            await tool(client, "remove_shader_node", material="Procedural", node="Math")
            with pytest.raises(ToolError):
                await tool(client, "configure_shader_node", material="Procedural", node={"name": "Noise", "type": "ShaderNodeTexNoise", "properties": {"operation": "arbitrary"}})
            assert len((await tool(client, "inspect_shader_graph", material="Procedural"))["nodes"]) == 4
            for preset in ("PBR", "GLASS", "EMISSION"):
                value = await tool(client, "apply_material_preset", material=preset, preset=preset)
                assert value["preset"] == preset
            textured = await tool(client, "apply_material_preset", material="PaintedPBR", preset="TEXTURED_PBR",
                base_color_texture="paint.png", roughness_texture="rough.png", metallic_texture="metal.png", normal_texture="normal.png")
            images = [n["image"] for n in textured["nodes"] if "image" in n]
            assert len(images) == 4 and all(i["packed"] for i in images)
            assert next(n["image"]["color_space"] for n in textured["nodes"] if n["name"] == "BaseColor") == "sRGB"
            assert all(n["image"]["color_space"] == "Non-Color" for n in textured["nodes"] if "image" in n and n["name"] != "BaseColor")
            await tool(client, "create_primitive", name="ShaderSphere", kind="UV_SPHERE", location=[20, 0, 1])
            await tool(client, "assign_material", objects=["ShaderSphere"], material="PaintedPBR")
            await tool(client, "configure_render", resolution_x=192, resolution_y=192, samples=8)
            stage = (await client.call_tool("run_action", {"action": "preview_setup", "inputs": {"objects": ["ShaderSphere"], "prefix": "Shader"}, "wait": True})).data
            assert stage["state"] == "succeeded", stage
            rendered = (await client.call_tool("submit_operation", {"command": "render_still", "params": {"path": "pbr.png", "camera": "Shader_Camera", "engine": "CYCLES", "width": 192, "height": 192}})).data
            complete = (await client.call_tool("wait_operation", {"operation_id": rendered["operation_id"], "timeout_seconds": 60})).data
            assert complete["state"] == "succeeded", complete
            await tool(client, "export_objects_glb", objects=["ShaderSphere"], path="pbr.glb")
            data = (library / "pbr.glb").read_bytes()
            gltf = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
            mat = gltf["materials"][0]
            assert mat["normalTexture"] and mat["pbrMetallicRoughness"]["baseColorTexture"] and mat["pbrMetallicRoughness"]["metallicRoughnessTexture"]
            await client.call_tool("save_blend", {"path": "shaders.blend"})
            await client.call_tool("open_blend", {"path": "shaders.blend"})
            assert len((await tool(client, "inspect_shader_graph", material="PaintedPBR"))["nodes"]) == len(textured["nodes"])
            if destination := os.environ.get("MCPBLENDER_SHADER_EVIDENCE_DIR"):
                target = Path(destination)
                target.mkdir(parents=True, exist_ok=True)
                for name in ("pbr.png", "pbr.glb", "shaders.blend"):
                    shutil.copy2(library / name, target / name)
    asyncio.run(run())

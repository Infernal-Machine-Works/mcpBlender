"""Real structured asset editing through MCP with arbitrary Python disabled."""
import asyncio
import os
from pathlib import Path
import shutil
import sys
import tempfile

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
import pytest

from mcpblender.config import Config, write_config
from mcpblender.launcher import Launcher
from mcpblender.wire import call

pytestmark = [pytest.mark.blender, pytest.mark.skipif(
    os.environ.get("MCPBLENDER_TEST_BLENDER") != "1" or not shutil.which("blender"),
    reason="Set MCPBLENDER_TEST_BLENDER=1 to use an installed Blender")]


@pytest.fixture(scope="module")
def assets_live():
    with tempfile.TemporaryDirectory(prefix="mb-asset-") as directory:
        root = Path(directory)
        config = Config()
        config.bridge.socket_path = str(root / "bridge/bridge.sock")
        config.bridge.timeout_seconds = 90
        config.launch.background = True
        config.launch.auto_recover = False
        config.launch.log_path = str(root / "blender.log")
        config.stdio.library_root = str(root / "library")
        config.stdio.allow_code_execution = False
        path = root / "config.json"
        write_config(path, config)
        launcher = Launcher(config)
        try:
            launcher.start()
            yield root, path
        finally:
            if launcher.process and launcher.process.poll() is None:
                launcher.process.terminate()
                launcher.process.wait(timeout=15)
            call(config.bridge.socket_path, "shutdown", timeout=5)


def client_for(path):
    return Client(StdioTransport(command=sys.executable,
        args=["-m", "mcpblender.server", "--config", str(path)]), timeout=100)


async def tool(client, command, **params):
    return (await client.call_tool(command, {"params": params})).data


def test_evaluated_mesh_and_modifier_lifecycle(assets_live):
    _, path = assets_live

    async def run():
        async with client_for(path) as client:
            assert "execute_code" not in {t.name for t in await client.list_tools()}
            with pytest.raises(ToolError):
                await client.call_tool("execute_code", {"code": "raise Exception('unreachable')"})
            await tool(client, "create_primitive", name="BeveledAsset", location=[3, 0, 1])
            base = await tool(client, "inspect_mesh", name="BeveledAsset", evaluated=False)
            assert (base["vertices"], base["polygons"], base["triangles"]) == (8, 6, 12)
            assert base["world_bounds"] == {"min": [2, -1, 0], "max": [4, 1, 2]}
            assert base["topology"]["non_manifold_edges"] == 0
            assert base["uv_layers"][0]["zero_area_faces"] == 0
            selected = (await client.call_tool("get_object_info", {"name": "Cube"})).data["selected"]
            await tool(client, "configure_modifier", name="BeveledAsset", modifier="SoftEdges",
                       settings={"type": "BEVEL", "width": .2, "segments": 3})
            evaluated = await tool(client, "inspect_mesh", name="BeveledAsset", sample_limit=3)
            assert evaluated["vertices"] > base["vertices"]
            assert len(evaluated["vertex_samples"]) == 3
            assert evaluated["geometry_sha256"] != base["geometry_sha256"]
            unchanged = await tool(client, "inspect_mesh", name="BeveledAsset", evaluated=False)
            assert unchanged["geometry_sha256"] == base["geometry_sha256"]
            with pytest.raises(ToolError, match="type must match"):
                await tool(client, "configure_modifier", name="BeveledAsset", modifier="SoftEdges",
                           settings={"type": "SUBSURF"})
            applied = await tool(client, "apply_modifier", name="BeveledAsset", modifier="SoftEdges")
            assert applied["vertices"] == evaluated["vertices"] and not applied["modifiers"]
            assert (await client.call_tool("get_object_info", {"name": "Cube"})).data["selected"] == selected
            for kind in ("SUBSURF", "DECIMATE", "SOLIDIFY", "TRIANGULATE"):
                await tool(client, "configure_modifier", name="BeveledAsset", modifier="Temporary",
                           settings={"type": kind})
                assert (await tool(client, "inspect_mesh", name="BeveledAsset"))["vertices"] > 0
                await tool(client, "remove_modifier", name="BeveledAsset", modifier="Temporary")
            with pytest.raises(ToolError, match="not found"):
                await tool(client, "apply_modifier", name="BeveledAsset", modifier="Temporary")
            await tool(client, "create_mesh", name="OpenTriangle", vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]])
            triangle = await tool(client, "inspect_mesh", name="OpenTriangle")
            assert triangle["topology"]["boundary_edges"] == 3
            assert not triangle["uv_layers"]

    asyncio.run(run())


def test_uv_projection_and_overwrite_protection(assets_live):
    _, path = assets_live

    async def run():
        async with client_for(path) as client:
            await tool(client, "create_mesh", name="UVPanel", vertices=[[0, 0, 0], [2, 0, 0], [2, 1, 0], [0, 1, 0]], faces=[[0, 1, 2, 3]])
            before = await tool(client, "inspect_mesh", name="UVPanel", evaluated=False)
            projected = await tool(client, "generate_uv", name="UVPanel", uv_map="GameUV")
            assert projected["geometry_sha256"] == before["geometry_sha256"]
            assert projected["uv_layers"][0]["name"] == "GameUV"
            assert projected["uv_layers"][0]["zero_area_faces"] == 0
            assert projected["uv_layers"][0]["non_finite_loops"] == 0
            bounds = projected["uv_layers"][0]["bounds"]
            assert all(-1e-6 <= v <= 1.000001 for v in bounds["min"] + bounds["max"])
            with pytest.raises(ToolError, match="already exists"):
                await tool(client, "generate_uv", name="UVPanel", uv_map="GameUV")
            cube = await tool(client, "generate_uv", name="UVPanel", uv_map="GameUV", overwrite=True, method="CUBE")
            assert cube["uv_layers"][0]["zero_area_faces"] == 0
            await tool(client, "generate_uv", name="UVPanel", uv_map="SecondUV")
            assert len((await tool(client, "inspect_mesh", name="UVPanel"))["uv_layers"]) == 2
            await client.call_tool("save_blend", {"path": "uv-panel.blend"})
            await client.call_tool("open_blend", {"path": "uv-panel.blend"})
            reopened = await tool(client, "inspect_mesh", name="UVPanel")
            assert [layer["name"] for layer in reopened["uv_layers"]] == ["GameUV", "SecondUV"]
            assert all(layer["zero_area_faces"] == 0 for layer in reopened["uv_layers"])

    asyncio.run(run())


def test_duplication_parenting_and_applied_transforms(assets_live):
    _, path = assets_live

    async def run():
        async with client_for(path) as client:
            await tool(client, "create_primitive", name="AssetParent", scale=[2, 3, 1], location=[3, 4, 0])
            await client.call_tool("set_object_transform", {"name": "AssetParent", "rotation_euler": [.2, .3, .5]})
            await tool(client, "create_primitive", name="AssetChild", location=[7, 2, 1])
            original = await tool(client, "inspect_mesh", name="AssetChild")
            parented = await tool(client, "set_parent", name="AssetChild", parent="AssetParent")
            assert parented["parent"] == "AssetParent"
            after_parent = await tool(client, "inspect_mesh", name="AssetChild")
            for key in ("min", "max"):
                assert after_parent["world_bounds"][key] == pytest.approx(original["world_bounds"][key], abs=1e-5)
            with pytest.raises(ToolError, match="cycle"):
                await tool(client, "set_parent", name="AssetParent", parent="AssetChild")
            before = await tool(client, "inspect_mesh", name="AssetParent")
            await tool(client, "apply_transforms", name="AssetParent")
            applied = (await client.call_tool("get_object_info", {"name": "AssetParent"})).data
            assert applied["scale"] == pytest.approx([1, 1, 1])
            assert applied["rotation_euler"] == pytest.approx([0, 0, 0])
            for name, expected in (("AssetParent", before), ("AssetChild", original)):
                actual = await tool(client, "inspect_mesh", name=name)
                for key in ("min", "max"):
                    assert actual["world_bounds"][key] == pytest.approx(expected["world_bounds"][key], abs=1e-5)
            unparented = await tool(client, "set_parent", name="AssetChild")
            assert unparented["parent"] is None
            copy = await tool(client, "duplicate_object", name="AssetParent", new_name="IndependentCopy", offset=[0, 0, 5])
            assert not copy["linked_data"]
            await tool(client, "configure_modifier", name="IndependentCopy", modifier="IndependentBevel", settings={"type": "BEVEL"})
            await tool(client, "apply_modifier", name="IndependentCopy", modifier="IndependentBevel")
            assert (await tool(client, "inspect_mesh", name="AssetParent", evaluated=False))["vertices"] == 8
            assert (await tool(client, "inspect_mesh", name="IndependentCopy"))["vertices"] > 8
            linked = await tool(client, "duplicate_object", name="AssetChild", new_name="LinkedCopy", linked_data=True)
            assert linked["linked_data"]
            with pytest.raises(ToolError, match="already exists"):
                await tool(client, "duplicate_object", name="AssetChild", new_name="LinkedCopy")
            for command, extra in (("apply_transforms", {}), ("generate_uv", {"overwrite": True})):
                with pytest.raises(ToolError, match="shared"):
                    await tool(client, command, name="LinkedCopy", **extra)
            await tool(client, "configure_modifier", name="LinkedCopy", modifier="SharedBevel", settings={"type": "BEVEL"})
            with pytest.raises(ToolError, match="shared"):
                await tool(client, "apply_modifier", name="LinkedCopy", modifier="SharedBevel")
            await tool(client, "apply_modifier", name="LinkedCopy", modifier="SharedBevel", make_single_user=True)
            assert (await tool(client, "inspect_mesh", name="AssetChild"))["vertices"] == 8
            await tool(client, "set_keyframes", name="IndependentCopy", data_path="location",
                       keyframes=[{"frame": 1, "value": [0, 0, 0]}, {"frame": 2, "value": [1, 0, 0]}])
            with pytest.raises(ToolError, match="Static"):
                await tool(client, "apply_transforms", name="IndependentCopy")

    asyncio.run(run())

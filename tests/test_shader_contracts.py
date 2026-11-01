import asyncio

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from mcpblender.config import Config
from mcpblender.server import build_server
from mcpblender.shader_contracts import ShaderGraph, ShaderNode


def test_shader_types_paths_and_readonly_discovery(tmp_path):
    for spec in ({"name": "Script", "type": "ShaderNodeScript"},
                 {"name": "Noise", "type": "ShaderNodeTexNoise", "image_path": "anything"},
                 {"name": "Value", "type": "ShaderNodeValue", "output_value": float("nan")}):
        with pytest.raises(ValueError):
            ShaderNode.model_validate(spec)
    with pytest.raises(ValueError):
        ShaderGraph.model_validate({"material": "M", "nodes": [{"name": "N", "type": "ShaderNodeValue"}] * 2})
    config = Config()
    config.stdio.library_root = str(tmp_path / "library")
    config.stdio.allow_scene_edit = False
    config.bridge.socket_path = str(tmp_path / "private/bridge.sock")
    server, state = build_server(config)
    with pytest.raises(ValueError, match="library_root"):
        state.prepare("apply_material_preset", {"material": "M", "preset": "TEXTURED_PBR", "normal_texture": "../outside.png"})
    async def run():
        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
            assert {"shader_node_catalog", "inspect_shader_graph"} <= names
            assert not names & {"configure_shader_graph", "configure_shader_node", "apply_material_preset"}
            with pytest.raises(ToolError):
                await client.call_tool("submit_operation", {"command": "apply_material_preset", "params": {"material": "M"}})
    asyncio.run(run())
    assert not (tmp_path / "private/bridge.sock").exists()

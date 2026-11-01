import asyncio

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from mcpblender.commands import COMMANDS
from mcpblender.config import Config
from mcpblender.server import build_server


def test_modifier_contract_rejects_unstructured_properties():
    model = COMMANDS["configure_modifier"][0]
    for settings in ({"type": "NODES"}, {"type": "BEVEL", "python": "anything"},
                     {"type": "BEVEL", "segments": 0}, {"type": "SUBSURF", "levels": 10}):
        with pytest.raises(ValueError):
            model.model_validate({"name": "Cube", "modifier": "Test", "settings": settings})


def test_asset_inspection_visible_without_edit_permission(tmp_path):
    config = Config()
    config.bridge.socket_path = str(tmp_path / "bridge/bridge.sock")
    config.stdio.library_root = str(tmp_path / "library")
    config.stdio.allow_scene_edit = False
    config.stdio.allow_code_execution = False
    server, state = build_server(config)

    async def run():
        async with Client(server) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert "inspect_mesh" in names
            for command in ("configure_modifier", "apply_modifier", "remove_modifier", "generate_uv",
                            "duplicate_object", "set_parent", "apply_transforms", "frame_objects"):
                assert command not in names
                with pytest.raises(PermissionError):
                    state.check(command)
            with pytest.raises(ToolError):
                await client.call_tool("submit_operation", {"command": "remove_modifier", "params": {"name": "Cube", "modifier": "Test"}})

    asyncio.run(run())

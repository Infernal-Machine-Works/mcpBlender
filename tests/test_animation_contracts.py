import asyncio

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from mcpblender.commands import COMMANDS
from mcpblender.config import Config
from mcpblender.server import build_server


def test_invalid_skeletons_and_weight_contracts():
    bone = {"name": "Root", "head": [0, 0, 0], "tail": [0, 0, 1]}
    model = COMMANDS["create_armature"][0]
    for bones in ([bone, bone], [bone | {"tail": [0, 0, 0]}], [bone | {"parent": "Missing"}],
                  [bone | {"parent": "Root"}], [bone | {"connected": True}],
                  [bone | {"parent": "Other"}, bone | {"name": "Other", "parent": "Root"}]):
        with pytest.raises(ValueError):
            model.model_validate({"name": "Rig", "bones": bones})
    binding = COMMANDS["bind_mesh"][0]
    for params in ({"method": "RIGID"}, {"method": "AUTO", "bone": "Root"}, {"method": "EXPLICIT"},
                   {"method": "EXPLICIT", "weights": [{"vertex": 0, "weights": {"Root": -1}}]},
                   {"method": "EXPLICIT", "weights": [{"vertex": 0, "weights": {"Root": 1}}] * 2}):
        with pytest.raises(ValueError):
            binding.model_validate({"name": "Mesh", "armature": "Rig"} | params)


def test_animation_readonly_policy(tmp_path):
    config = Config()
    config.bridge.socket_path = str(tmp_path / "bridge/bridge.sock")
    config.stdio.library_root = str(tmp_path / "library")
    config.stdio.allow_scene_edit = False
    config.stdio.allow_code_execution = False
    server, state = build_server(config)

    async def run():
        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
            assert "inspect_rig" in names
            for command in ("create_armature", "bind_mesh"):
                assert command not in names
                with pytest.raises(PermissionError):
                    state.check(command)
            with pytest.raises(ToolError):
                await client.call_tool("submit_operation", {"command": "create_armature", "params": {}})

    asyncio.run(run())

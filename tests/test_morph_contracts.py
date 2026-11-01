import asyncio

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from mcpblender.commands import COMMANDS
from mcpblender.config import Config
from mcpblender.server import build_server


def test_shape_contracts_reject_ambiguous_or_nonfinite_edits():
    model = COMMANDS["create_shape_key"][0]
    vertex = {"vertex": 0, "value": [0, 0, 1]}
    for extra in ({"vertices": [vertex, vertex]}, {"vertices": [vertex], "source_object": "Other"},
                  {"vertices": [{"vertex": -1, "value": [0, 0, 0]}]},
                  {"vertices": [{"vertex": 0, "value": [float("nan"), 0, 0]}]},
                  {"expected_topology": "not-a-digest"}, {"key": "ü" * 40}):
        with pytest.raises(ValueError):
            model.model_validate({"name": "Mesh", "key": "Wide"} | extra)
    with pytest.raises(ValueError):
        COMMANDS["edit_shape_key"][0].model_validate({"name": "Mesh", "key": "Wide"})
    with pytest.raises(ValueError):
        COMMANDS["set_shape_key_weights"][0].model_validate({"name": "Mesh", "values": {"Wide": 1.1}})


def test_morph_readonly_policy(tmp_path):
    config = Config()
    config.bridge.socket_path = str(tmp_path / "bridge/bridge.sock")
    config.stdio.library_root = str(tmp_path / "library")
    config.stdio.allow_scene_edit = False
    config.stdio.allow_code_execution = False
    server, state = build_server(config)

    async def run():
        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
            assert "inspect_shape_keys" in names
            for command in ("create_shape_key", "edit_shape_key", "set_shape_key_weights", "remove_shape_key"):
                assert command not in names
                with pytest.raises(PermissionError):
                    state.check(command)
            with pytest.raises(ToolError):
                await client.call_tool("submit_operation", {"command": "set_shape_key_weights", "params": {"name": "Mesh", "values": {"Wide": 1}}})

    asyncio.run(run())

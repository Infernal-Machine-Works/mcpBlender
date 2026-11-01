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
            assert {"inspect_shape_keys", "inspect_morph_animation", "sample_morph_animation"} <= names
            for command in ("create_shape_key", "edit_shape_key", "set_shape_key_weights", "remove_shape_key",
                            "create_morph_clip", "set_morph_keyframes", "set_active_morph_clip"):
                assert command not in names
                with pytest.raises(PermissionError):
                    state.check(command)
            with pytest.raises(ToolError):
                await client.call_tool("submit_operation", {"command": "set_shape_key_weights", "params": {"name": "Mesh", "values": {"Wide": 1}}})

    asyncio.run(run())


def test_morph_tracks_are_scalar_bounded_and_unique():
    model = COMMANDS["create_morph_clip"][0]
    track = {"key": "Wide", "keyframes": [{"frame": 1, "value": .5}]}
    for tracks in ([track, track], [track | {"keyframes": [{"frame": 1, "value": .5}] * 2}],
                   [track | {"keyframes": [{"frame": 1, "value": 1.1}]}],
                   [track | {"keyframes": [{"frame": 2_000_000, "value": 1}]}],
                   [track | {"data_path": "arbitrary.path"}]):
        with pytest.raises(ValueError):
            model.model_validate({"name": "Mesh", "clip": "Pulse", "tracks": tracks})
    with pytest.raises(ValueError):
        COMMANDS["sample_morph_animation"][0].model_validate({"name": "Mesh", "frames": [1], "skeletal_clip": "Wave"})

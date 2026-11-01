import asyncio
import json
from pathlib import Path

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from mcpblender.actions import Registry
from mcpblender.config import Config, load_config, write_config
from mcpblender.server import build_server
from mcpblender.storage import estimate


def recipe(name="custom.make_prop", steps=None):
    return {"name": name, "version": "1", "description": "Create and inspect a named prop.",
            "inputs": {"name": {"type": "string", "description": "Unique object name."}},
            "steps": steps or [
                {"id": "create", "command": "create_primitive", "params": {"name": {"$input": "name"}}},
                {"id": "inspect", "command": "inspect_mesh", "params": {"name": {"$input": "name"}}}]}


def recipe_file(path, recipes):
    path.write_text(json.dumps({"format_version": 1, "actions": recipes}))
    return path


def configured(tmp_path, recipes=None):
    config = Config()
    config.bridge.socket_path = str(tmp_path / "bridge/bridge.sock")
    config.stdio.library_root = str(tmp_path / "library")
    config.action_files = [str(recipe_file(tmp_path / "actions.json", recipes or [recipe()]))]
    return config


def test_recipe_validation_and_typed_plan(tmp_path):
    config = configured(tmp_path)
    _, state = build_server(config)
    plan = state.compile_action("custom.make_prop", {"name": "Prop"}, "stable-id")
    assert plan["steps"][0]["params"]["name"] == "Prop"
    assert len(plan["definition_sha256"]) == 64
    for inputs in ({"name": 42}, {"name": "Prop", "code": "anything"}, {}):
        with pytest.raises(ValueError):
            state.compile_action("custom.make_prop", inputs, "stable-id")
    assert not Path(config.bridge.socket_path).exists()


@pytest.mark.parametrize("command", ["execute_code", "run_action", "shutdown_blender", "request_asset_api", "open_blend"])
def test_recipe_escape_commands_rejected_even_with_full_policy(tmp_path, command):
    path = recipe_file(tmp_path / "bad.json", [recipe(steps=[{"id": "escape", "command": command, "params": {}}])])
    with pytest.raises(ValueError, match="not allowed"):
        Registry([str(path)])


def test_invalid_templates_duplicates_and_request_payloads(tmp_path):
    for params in ({"name": {"$input": "missing"}}, {"name": {"$eval": "anything"}}, {"name": {"$input": "name", "extra": True}}):
        path = recipe_file(tmp_path / "bad.json", [recipe(steps=[{"id": "one", "command": "create_primitive", "params": params}])])
        with pytest.raises(ValueError):
            Registry([str(path)])
    path = recipe_file(tmp_path / "duplicate.json", [recipe(), recipe()])
    with pytest.raises(ValueError, match="Duplicate action"):
        Registry([str(path)])
    config = configured(tmp_path)
    _, state = build_server(config)
    with pytest.raises(ValueError):
        state.prepare("run_action", {"action": "custom.make_prop", "inputs": {"name": "Prop"}, "steps": [{"command": "execute_code"}]})


def test_action_policy_discovery_and_direct_submission(tmp_path):
    config = configured(tmp_path)
    config.stdio.allow_scene_edit = False
    server, _ = build_server(config)

    async def run():
        async with Client(server) as client:
            available = {a["name"] for a in (await client.call_tool("list_actions")).data["actions"]}
            assert "custom.make_prop" not in available and "preview_setup" not in available
            assert "game_asset_prepare" in available
            for tool, args in (("describe_action", {"action": "custom.make_prop"}),
                               ("run_action", {"action": "custom.make_prop", "inputs": {"name": "Denied"}}),
                               ("submit_operation", {"command": "run_action", "params": {"action": "custom.make_prop", "inputs": {"name": "Denied"}}})):
                with pytest.raises(ToolError):
                    await client.call_tool(tool, args)
        assert not Path(config.bridge.socket_path).exists()
    asyncio.run(run())


def test_preflight_all_permissions_paths_and_stable_outputs(tmp_path):
    action = recipe(steps=[{"id": "create", "command": "create_primitive", "params": {"name": {"$input": "name"}}},
                           {"id": "render", "command": "render_still", "params": {"width": 64, "height": 64}}])
    config = configured(tmp_path, [action])
    _, state = build_server(config)
    one = state.compile_action(action["name"], {"name": "Prop"}, "same")
    assert one == state.compile_action(action["name"], {"name": "Prop"}, "same")
    assert one["steps"][1]["params"]["path"].endswith(".png")
    size, kind = estimate("run_action", one)
    assert size >= 64 * 64 * 8 and "unknown" in kind
    config.stdio.allow_snapshots = False
    with pytest.raises(PermissionError):
        state.compile_action(action["name"], {"name": "Prop"}, "denied")
    config.stdio.allow_snapshots = True
    action["steps"][1]["params"]["path"] = "../escape.png"
    configured(tmp_path, [action])
    _, other = build_server(config)
    with pytest.raises(ValueError, match="library_root"):
        other.compile_action(action["name"], {"name": "Prop"}, "bad-path")


def test_recipe_paths_relative_to_config(tmp_path):
    config = configured(tmp_path)
    config.action_files = ["actions.json"]
    path = tmp_path / "server.json"
    write_config(path, config)
    assert load_config(path).action_files == [str(tmp_path / "actions.json")]


def test_preview_builtin_typed_plan_and_permissions(tmp_path):
    config = configured(tmp_path)
    _, state = build_server(config)
    plan = state.compile_action("preview_setup", {"objects": ["Prop"], "prefix": "Studio"}, "stage")
    assert plan["steps"][0]["command"] == "setup_preview_stage"
    assert plan["steps"][0]["params"]["projection"] == "PERSP"
    for inputs in ({"objects": []}, {"objects": ["Prop"], "prefix": "../escape"},
                   {"objects": ["Prop"], "strength": 0}):
        with pytest.raises(ValueError):
            state.compile_action("preview_setup", inputs, "invalid")
    config.stdio.allow_scene_edit = False
    with pytest.raises(PermissionError):
        state.compile_action("preview_setup", {"objects": ["Prop"]}, "denied")
    assert not Path(config.bridge.socket_path).exists()


def test_game_asset_optional_permissions_and_paths(tmp_path):
    config = configured(tmp_path)
    config.stdio.allow_scene_edit = False
    config.stdio.allow_library_write = False
    _, state = build_server(config)
    plan = state.compile_action("game_asset_prepare", {"objects": ["Prop"]}, "inspect")
    assert [s["command"] for s in plan["steps"]] == ["preflight_game_asset", "asset_report", "asset_report"]
    for inputs in ({"objects": ["Prop"], "generate_uv": True}, {"objects": ["Prop"], "export_path": "prop.glb"}):
        with pytest.raises(PermissionError):
            state.compile_action("game_asset_prepare", inputs, "denied")
    config.stdio.allow_library_write = True
    with pytest.raises(ValueError, match="library_root"):
        state.compile_action("game_asset_prepare", {"objects": ["Prop"], "export_path": "../bad.glb"}, "path")
    config.stdio.allow_scene_edit = True
    with pytest.raises(ValueError):
        state.compile_action("game_asset_prepare", {"objects": ["Prop", "Prop"]}, "duplicate")
    plan = state.compile_action("game_asset_prepare", {"objects": ["Prop"], "generate_uv": True,
        "triangulate": True, "apply_rotation_scale": True, "export_path": "prop.glb", "save_path": "prop.blend"}, "edits")
    assert len(plan["steps"]) == 8
    assert plan["steps"][-1]["params"]["path"].endswith("/library/prop.glb")


def test_animation_preview_plan_storage_and_permissions(tmp_path):
    config = configured(tmp_path)
    _, state = build_server(config)
    plan = state.compile_action("animation_preview", {"frame_start": 1, "frame_end": 3, "directory": "preview"}, "render")
    params = plan["steps"][0]["params"]
    assert params["path"].endswith("/library/preview/preview.mp4")
    assert not Path(params["directory"]).exists()
    size, kind = estimate("run_action", plan)
    assert size > 640 * 360 * 8 * 3
    for inputs in ({"frame_start": 4, "frame_end": 1}, {"frame_start": 1, "frame_end": 4, "frame_step": 2},
                   {"frame_start": 1, "frame_end": 3, "width": 65}):
        with pytest.raises(ValueError):
            state.compile_action("animation_preview", inputs, "invalid")
    config.stdio.allow_snapshots = False
    with pytest.raises(PermissionError):
        state.compile_action("animation_preview", {"frame_start": 1, "frame_end": 3}, "denied")

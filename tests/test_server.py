import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcpblender.config import Config, generate_key, load_config, match_key, write_config
from mcpblender.server import ACTIVE_KEY, build_http_app, build_server


def configured(tmp_path):
    path = tmp_path / "server.json"
    config = Config()
    config.stdio.library_root = str(tmp_path / "library")
    config.bridge.socket_path = str(tmp_path / "private/bridge.sock")
    write_config(path, config)
    token = generate_key(path, "agent")
    config = load_config(path)
    config.api_keys[0].library_root = str(tmp_path / "remote-library")
    return config, token, path


def test_key_generation_rotation_and_freecad_compatibility(tmp_path):
    config, token, path = configured(tmp_path)
    assert token not in path.read_text()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert match_key(token, config.api_keys).id == "agent"
    assert match_key("wrong", config.api_keys) is None
    # Verify the same KDF fields/hash can be consumed by the sibling server.
    import importlib.util
    sibling_path = os.environ.get("MCPBLENDER_TEST_FREECAD_KEY_MODULE")
    if sibling_path:
        sibling = Path(sibling_path)
        assert sibling.is_file(), "Configured FreeCAD key module does not exist"
        spec = importlib.util.spec_from_file_location("freecad_key_utils", sibling)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.verify_token_against_key(token, config.api_keys[0])
    rotated = generate_key(path, "agent")
    current = load_config(path)
    assert match_key(token, current.api_keys) is None
    assert match_key(rotated, current.api_keys).id == "agent"


def test_auth_formats_and_artifact_ownership(tmp_path):
    config, token, path = configured(tmp_path)
    other = generate_key(path, "other")
    config.api_keys.append(load_config(path).api_keys[-1])
    config.mode = "remotehttp"
    server, state = build_server(config)
    context = ACTIVE_KEY.set(config.api_keys[0])
    image = tmp_path / "image.png"
    image.write_bytes(b"test-image")
    artifact = state.artifact(image)
    ACTIVE_KEY.reset(context)
    with TestClient(build_http_app(server, state)) as client:
        assert client.get("/status").status_code == 200
        assert client.get("/mcp/").status_code == 401
        assert client.get("/mcp/?mcp=invalid").status_code == 401
        url = "/artifacts/" + artifact["artifact_id"]
        for suffix, headers in [
            ("?mcp=" + token, {}), ("?api_key=" + token, {}),
            ("", {"Authorization": "Bearer " + token}), ("", {"X-API-Key": token}),
        ]:
            response = client.get(url + suffix, headers=headers)
            assert response.status_code == 200
            assert response.content == b"test-image"
        assert client.get(url, headers={"X-API-Key": other}).status_code == 404
        record = state.artifacts[artifact["artifact_id"]]
        assert client.get(url + "?token=" + record["token"]).status_code == 200
        assert client.get(url + "?token=bad").status_code == 404
        record["expires"] = 0
        state.artifacts[artifact["artifact_id"]] = record
        assert client.get(url + "?token=" + record["token"]).status_code == 404


def test_policy_discovery_and_direct_call_denial(tmp_path):
    config, _, _ = configured(tmp_path)
    config.stdio.allow_code_execution = False
    config.stdio.allow_library_write = False
    config.stdio.allow_start_blender = False
    server, _ = build_server(config)

    async def run():
        async with Client(server) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert "get_scene_info" in names
            assert not names & {"execute_code", "save_blend", "export_glb", "start_blender"}
            with pytest.raises(ToolError):
                await client.call_tool("execute_code", {"code": "raise Exception('must not execute')"})

    asyncio.run(run())


def test_library_traversal_and_symlink_denied(tmp_path):
    config, _, _ = configured(tmp_path)
    _, state = build_server(config)
    root = Path(config.stdio.library_root)
    root.mkdir()
    (root / "escape").symlink_to(tmp_path)
    for candidate in ("../secret", "escape/secret", str(tmp_path / "secret")):
        with pytest.raises(ValueError):
            state.library_path(candidate, write=True)
    assert state.library_path("models/part.glb", write=True) == root / "models/part.glb"


def test_remote_context_does_not_fall_back_to_stdio(tmp_path):
    config, _, _ = configured(tmp_path)
    config.mode = "remotehttp"
    _, state = build_server(config)
    with pytest.raises(PermissionError):
        state.policy()


def test_duplicate_key_ids_rejected(tmp_path):
    config, _, _ = configured(tmp_path)
    data = config.model_dump()
    data["api_keys"].append(data["api_keys"][0].copy())
    with pytest.raises(ValueError, match="unique"):
        Config.model_validate(data)


def test_addon_archive_has_no_external_dependencies(tmp_path):
    from mcpblender.server import addon_zip
    import zipfile
    path = tmp_path / "addon.zip"
    addon_zip(path)
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert "mcpblender/__init__.py" in names
        assert "mcpblender/wire.py" in names
        assert "mcpblender/blender/bridge.py" in names
        assert "mcpblender/server.py" not in names

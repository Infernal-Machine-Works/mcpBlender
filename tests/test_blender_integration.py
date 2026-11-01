"""Opt-in live tests use a disposable Blender process and private /tmp workspace."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError

from mcpblender.config import Config, generate_key, load_config, write_config
from mcpblender.launcher import Launcher
from mcpblender.wire import call

pytestmark = [pytest.mark.blender, pytest.mark.skipif(
    os.environ.get("MCPBLENDER_TEST_BLENDER") != "1" or not shutil.which("blender"),
    reason="Set MCPBLENDER_TEST_BLENDER=1 to use an installed Blender")]


@pytest.fixture(scope="module")
def live():
    with tempfile.TemporaryDirectory(prefix="mb-test-") as directory:
        root = Path(directory)
        config = Config()
        config.bridge.socket_path = str(root / "bridge/bridge.sock")
        config.bridge.timeout_seconds = 90
        config.launch.background = True
        config.launch.auto_recover = False
        config.launch.log_path = str(root / "blender.log")
        config.stdio.library_root = str(root / "library")
        path = root / "config.json"
        write_config(path, config)
        launcher = Launcher(config)
        try:
            status = launcher.start()
            assert status["started"]
            assert status["status"]["background"]
            assert not launcher.start()["started"]
            yield root, path, config, launcher
        finally:
            if launcher.process and launcher.process.poll() is None:
                launcher.process.terminate()
                launcher.process.wait(timeout=15)
            call(config.bridge.socket_path, "shutdown", timeout=5)


def test_live_stdio_create_inspect_save_reopen_export_render(live):
    root, path, config, _ = live

    async def run():
        transport = StdioTransport(command=sys.executable, args=["-m", "mcpblender.server", "--config", str(path)])
        async with Client(transport, timeout=100) as client:
            assert (await client.call_tool("bridge_status")).data["bridge"]["protocol"] == 2
            result = (await client.call_tool("execute_code", {"code": '''
import math
bpy.ops.mesh.primitive_cube_add(size=2, location=(1, 2, 0))
obj = bpy.context.object
obj.name = "MCP_TestPart"
material = bpy.data.materials.new("MCP_TestMaterial")
material.diffuse_color = (0.1, 0.5, 0.8, 1)
obj.data.materials.append(material)
modifier = obj.modifiers.new("Bevel", "BEVEL")
modifier.width = 0.1
modifier.segments = 2
bpy.context.scene.render.threads_mode = "FIXED"
bpy.context.scene.render.threads = 2
bpy.context.scene.cycles.samples = 1
bpy.context.scene.cycles.use_denoising = False
print("created", obj.name)
result = {"name": obj.name}
'''})).data
            assert result["success"] and "created MCP_TestPart" in result["stdout"]
            info = (await client.call_tool("get_object_info", {"name": "MCP_TestPart"})).data
            assert info["mesh"]["vertices"] == 8
            assert info["location"] == [1, 2, 0]
            assert info["modifiers"][0]["type"] == "BEVEL"
            await client.call_tool("set_object_transform", {"name": "MCP_TestPart", "location": [0, 0, 0]})
            await client.call_tool("select_objects", {"names": ["MCP_TestPart"]})
            exported = (await client.call_tool("export_glb", {"path": "part.glb"})).data
            assert Path(exported["path"]).read_bytes()[:4] == b"glTF"
            await client.call_tool("save_blend", {"path": "scene.blend"})
            with pytest.raises(ToolError):
                await client.call_tool("save_blend", {"path": "scene.blend"})
            await client.call_tool("execute_code", {"code": 'bpy.data.objects["MCP_TestPart"].location.x = 99'})
            await client.call_tool("open_blend", {"path": "scene.blend"})
            restored = (await client.call_tool("get_object_info", {"name": "MCP_TestPart"})).data
            assert restored["location"] == [0, 0, 0]
            await client.call_tool("import_asset", {"path": "part.glb"})
            objects = (await client.call_tool("get_scene_info")).data
            assert any(o["name"].startswith("MCP_TestPart.") for o in objects["objects"])
            error = (await client.call_tool("execute_code", {"code": 'print("before error"); raise ValueError("expected")'})).data
            assert not error["success"] and "ValueError: expected" in error["traceback"]
            assert "before error" in error["stdout"]
            job = (await client.call_tool("render_still", {"width": 128, "height": 128, "engine": "CYCLES"})).data
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                result = (await client.call_tool("get_job", {"job_id": job["job_id"]})).data
                if result["state"] in ("succeeded", "failed"):
                    break
                await asyncio.sleep(0.1)
            assert result["state"] == "succeeded", result
            png = Path(result["result"]["path"]).read_bytes()
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            image = await client.call_tool("get_image", {"artifact_id": result["result"]["artifact_id"]})
            assert any(block.type == "image" for block in image.content)
            with pytest.raises(ToolError, match="GUI"):
                await client.call_tool("capture_viewport")

    asyncio.run(run())


def test_live_http_auth_policy_and_images(live):
    root, _, config, _ = live
    path = root / "remote.json"
    write_config(path, config)
    token = generate_key(path, "full")
    readonly_token = generate_key(path, "read")
    config = load_config(path)
    config.mode = "remotehttp"
    for key in config.api_keys:
        key.library_root = str(root / key.id)
    readonly = config.api_keys[1]
    readonly.allow_code_execution = False
    readonly.allow_library_write = False
    readonly.allow_start_blender = False
    readonly.allow_scene_edit = False
    readonly.allow_snapshots = False
    readonly.allow_external_assets = False
    readonly.allow_shutdown_blender = False
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config.remote_server.port = port
    config.remote_server.url_prefix = f"http://127.0.0.1:{port}/"
    write_config(path, config)
    base = config.remote_server.url_prefix
    with open(root / "http.log", "wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "mcpblender.server", "--config", str(path)],
                                   stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    if httpx.get(base + "status").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail((root / "http.log").read_text())

            async def run():
                for query, headers in [("?mcp=" + token, {}), ("?api_key=" + token, {}),
                                       ("", {"Authorization": "Bearer " + token}), ("", {"X-API-Key": token})]:
                    transport = StreamableHttpTransport(base + "mcp/" + query, headers=headers)
                    async with Client(transport, timeout=100) as client:
                        names = {t.name for t in await client.list_tools()}
                        assert "execute_code" in names
                        assert (await client.call_tool("bridge_status")).data["key_id"] == "full"
                async with Client(StreamableHttpTransport(base + "mcp/?mcp=" + readonly_token)) as client:
                    names = {t.name for t in await client.list_tools()}
                    assert "get_scene_info" in names
                    assert not names & {"execute_code", "save_blend", "select_objects", "start_blender", "render_still"}
                    assert "objects" in (await client.call_tool("get_scene_info")).data
                    with pytest.raises(ToolError):
                        await client.call_tool("execute_code", {"code": "raise Exception('not reached')"})
                async with Client(StreamableHttpTransport(base + "mcp/?mcp=" + token), timeout=100) as client:
                    job = (await client.call_tool("render_still", {"width": 64, "height": 64, "engine": "CYCLES"})).data
                    for _ in range(900):
                        result = (await client.call_tool("get_job", {"job_id": job["job_id"]})).data
                        if result["state"] in ("succeeded", "failed"):
                            break
                        await asyncio.sleep(0.1)
                    assert result["state"] == "succeeded", result
                    artifact = result["result"]
                    assert httpx.get(artifact["download_url_with_token"]).content.startswith(b"\x89PNG")
                    assert httpx.get(artifact["download_url"], headers={"X-API-Key": token}).status_code == 200
                    assert httpx.get(artifact["download_url"], headers={"X-API-Key": readonly_token}).status_code == 404
                async with Client(StreamableHttpTransport(base + "mcp/?mcp=" + readonly_token)) as client:
                    with pytest.raises(ToolError):
                        await client.call_tool("get_job", {"job_id": job["job_id"]})

            asyncio.run(run())
        finally:
            process.terminate()
            process.wait(timeout=15)
    logs = (root / "http.log").read_text()
    assert token not in logs and readonly_token not in logs


def test_multiple_connections_disconnect_recovery_and_cancel(live):
    root, path, config, _ = live

    def transport():
        return StdioTransport(command=sys.executable, args=["-m", "mcpblender.server", "--config", str(path)])

    async def run():
        code = '''
import time
report_progress({"phase": "editing facade", "fraction": 0.2})
time.sleep(2)
bpy.context.scene["recovery_counter"] = bpy.context.scene.get("recovery_counter", 0) + 1
result = {"count": bpy.context.scene["recovery_counter"]}
'''
        async with Client(transport()) as observer:
            async with Client(transport()) as editor:
                first, duplicate = await asyncio.gather(*[
                    client.call_tool("execute_code", {"code": code, "wait": False, "request_id": "facade-edit-once"})
                    for client in (editor, observer)
                ])
                identifier = first.data["operation_id"]
                assert duplicate.data["operation_id"] == identifier
                for _ in range(30):
                    status = (await observer.call_tool("bridge_status")).data["bridge"]
                    if status["active_operation"] == identifier:
                        break
                    await asyncio.sleep(.05)
                assert status["active_operation"] == identifier
                pending = (await observer.call_tool("submit_operation", {
                    "command": "execute_code", "params": {"code": 'raise RuntimeError("cancelled work must not run")'},
                })).data
                cancelled = (await observer.call_tool("cancel_operation", {"operation_id": pending["operation_id"]})).data
                assert cancelled["state"] == "cancelled"
            # Editor frontend has exited; the observer and a new frontend recover the operation.
            async with Client(transport()) as reconnected:
                result = (await reconnected.call_tool("wait_operation", {"operation_id": identifier, "timeout_seconds": 10})).data
                assert result["state"] == "succeeded", result
                assert result["result"]["result"]["count"] == 1
                recovered = (await reconnected.call_tool("execute_code", {"code": code, "wait": False,
                                                                           "request_id": "facade-edit-once"})).data
                assert recovered["operation_id"] == identifier
                with pytest.raises(ToolError, match="different arguments"):
                    await reconnected.call_tool("execute_code", {"code": "result = 99", "wait": False,
                                                                  "request_id": "facade-edit-once"})
                job = (await reconnected.call_tool("render_still", {"width": 32, "height": 32, "engine": "CYCLES"})).data
                rendered = (await reconnected.call_tool("wait_operation", {"operation_id": job["operation_id"], "timeout_seconds": 30})).data
                assert rendered["state"] == "succeeded"
                artifact = rendered["result"]["artifact_id"]
            async with Client(transport()) as fresh:
                image = await fresh.call_tool("get_image", {"artifact_id": artifact})
                assert any(block.type == "image" for block in image.content)
                listed = (await fresh.call_tool("list_operations", {"limit": 100})).data
                assert any(op["operation_id"] == identifier for op in listed["operations"])

    asyncio.run(run())


def test_broker_restart_during_python_execution_does_not_repeat(live):
    import signal
    from mcpblender.broker import ensure_broker
    _, path, config, _ = live

    async def run():
        transport = StdioTransport(command=sys.executable, args=["-m", "mcpblender.server", "--config", str(path)])
        async with Client(transport) as client:
            operation = (await client.call_tool("execute_code", {"wait": False, "request_id": "broker-recovery",
                "code": 'import time; report_progress({"phase": "before broker restart"}); time.sleep(3); bpy.context.scene["broker_counter"] = bpy.context.scene.get("broker_counter", 0) + 1; result = {"count": bpy.context.scene["broker_counter"]}'})).data
            for _ in range(40):
                status = call(config.bridge.socket_path, "get_operation", {"operation_id": operation["operation_id"], "owner": "stdio"})
                if status.get("progress"):
                    break
                await asyncio.sleep(.05)
            assert status.get("progress")
            broker_pid = call(config.bridge.socket_path, "status")["broker_pid"]
            os.kill(broker_pid, signal.SIGTERM)
            await asyncio.sleep(.2)
            restarted = await asyncio.to_thread(ensure_broker, config.bridge.socket_path)
            assert restarted["broker_pid"] != broker_pid
            result = (await client.call_tool("wait_operation", {"operation_id": operation["operation_id"], "timeout_seconds": 15})).data
            assert result["state"] == "succeeded", result
            assert result["result"]["result"]["count"] == 1

    asyncio.run(run())


def test_blender_exit_records_unknown_outcome_without_replay(live):
    _, path, config, launcher = live

    async def run():
        transport = StdioTransport(command=sys.executable, args=["-m", "mcpblender.server", "--config", str(path)])
        async with Client(transport) as client:
            operation = (await client.call_tool("execute_code", {"wait": False, "request_id": "crash-edit",
                "code": 'import time; report_progress({"phase": "before process exit"}); time.sleep(30); bpy.context.scene["must_not_replay"] = True'})).data
            for _ in range(40):
                status = (await client.call_tool("get_operation", {"operation_id": operation["operation_id"]})).data
                if status.get("progress"):
                    break
                await asyncio.sleep(.05)
            launcher.process.terminate()
            launcher.process.wait(timeout=15)
            interrupted = (await client.call_tool("get_operation", {"operation_id": operation["operation_id"]})).data
            assert interrupted["state"] == "interrupted" and interrupted["outcome_unknown"]
            restarted = await asyncio.to_thread(launcher.start)
            assert restarted["started"] and restarted["status"]["session_id"] != interrupted["session_id"]
            check = (await client.call_tool("execute_code", {"code": 'result = bpy.context.scene.get("must_not_replay", False)'})).data
            assert check["result"] is False

    asyncio.run(run())


def test_automatic_checkpoint_recovery_bridge_reconnect_and_graceful_shutdown(live):
    import sqlite3
    _, path, config, launcher = live

    async def run():
        transport = StdioTransport(command=sys.executable, args=["-m", "mcpblender.server", "--config", str(path)])
        async with Client(transport, timeout=100) as client:
            await client.call_tool("create_primitive", {"params": {"name": "RecoveryMarker", "location": [4, 5, 6]}})
            checkpoint = (await client.call_tool("checkpoint")).data
            assert Path(checkpoint["checkpoint_path"]).is_file()
            before = (await client.call_tool("bridge_status")).data["bridge"]
            restarted_bridge = (await client.call_tool("restart_bridge")).data
            assert restarted_bridge["broker_epoch"] != before["broker_epoch"]
            assert restarted_bridge["pid"] == before["pid"]
            with sqlite3.connect(Path(config.bridge.socket_path).parent / "operations.sqlite3") as db:
                lifecycle = json.loads(db.execute("SELECT value FROM metadata WHERE name='lifecycle'").fetchone()[0])
            lifecycle.update(auto_recover=True, checkpoint_path=checkpoint["checkpoint_path"])
            call(config.bridge.socket_path, "configure_lifecycle", {"options": lifecycle})
            operation = (await client.call_tool("execute_code", {"wait": False,
                "code": 'import time; report_progress({"phase":"before crash"}); time.sleep(30); bpy.data.objects["RecoveryMarker"].location.x=999'})).data
            for _ in range(50):
                result = (await client.call_tool("get_operation", {"operation_id": operation["operation_id"]})).data
                if result.get("progress"):
                    break
                await asyncio.sleep(.05)
            assert result.get("progress")
            launcher.process.terminate()
            launcher.process.wait(timeout=15)
            # A real client can query during recovery startup, before worker
            # registration. It must attach or launch under the shared lock,
            # rather than racing the supervisor into a duplicate process.
            await client.call_tool("get_scene_info")
            recovered = (await client.call_tool("bridge_status")).data["bridge"]
            assert recovered["connected"] and recovered["pid"] != before["pid"]
            assert recovered["recovery"]["last_pid"] == recovered["pid"]
            assert recovered["recovered_from"] == checkpoint["checkpoint_path"]
            marker = (await client.call_tool("get_object_info", {"name": "RecoveryMarker"})).data
            assert marker["location"] == [4, 5, 6]
            interrupted = (await client.call_tool("get_operation", {"operation_id": operation["operation_id"]})).data
            assert interrupted["state"] == "interrupted" and interrupted["outcome_unknown"]
            stopped = (await client.call_tool("shutdown_blender", {"params": {"discard_unsaved": True}})).data
            assert stopped["shutting_down"]
            await asyncio.sleep(.5)
            after = (await client.call_tool("bridge_status")).data["bridge"]
            assert not after["connected"] and after["recovery"]["desired_running"] is False
            # A later editing request starts a fresh frontend-managed application lazily.
            await client.call_tool("get_scene_info")
            running = (await client.call_tool("bridge_status")).data["bridge"]
            assert running["connected"]
            await client.call_tool("shutdown_blender", {"params": {"discard_unsaved": True}})
            await asyncio.sleep(.5)

    asyncio.run(run())


def test_opt_in_frontend_exit_checkpoints_and_stops_owned_application():
    with tempfile.TemporaryDirectory(prefix="mb-owned-exit-") as directory:
        root = Path(directory)
        config = Config()
        config.bridge.socket_path = str(root / "bridge/bridge.sock")
        config.launch.background = True
        config.launch.shutdown_on_exit = True
        config.launch.log_path = str(root / "blender.log")
        config.stdio.library_root = str(root / "library")
        path = root / "config.json"
        write_config(path, config)

        async def run():
            transport = StdioTransport(command=sys.executable, args=["-m", "mcpblender.server", "--config", str(path)])
            async with Client(transport, timeout=60) as client:
                # This first operation exercises lazy startup rather than start_blender.
                result = (await client.call_tool("execute_code", {"code": 'bpy.ops.mesh.primitive_cube_add(); bpy.context.object.name="OwnedExitMarker"'})).data
                assert result["success"]

        try:
            asyncio.run(run())
            for _ in range(300):
                status = call(config.bridge.socket_path, "status")
                if not status["connected"] and status["recovery"]["desired_running"] is False:
                    break
                time.sleep(.1)
            assert not status["connected"] and status["recovery"]["desired_running"] is False
            assert (root / "bridge/recovery/latest.json").is_file()
        finally:
            status = call(config.bridge.socket_path, "status")
            call(config.bridge.socket_path, "configure_lifecycle", {"options": {"desired_running": False, "auto_recover": False}})
            if status["connected"]:
                os.kill(status["pid"], 15)
                for _ in range(100):
                    if not call(config.bridge.socket_path, "status")["connected"]:
                        break
                    time.sleep(.1)
            call(config.bridge.socket_path, "shutdown")

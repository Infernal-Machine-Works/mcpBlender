"""Named action execution in disposable Blender with Python execution disabled."""
import asyncio
import os
import shutil
import json
import struct
import zlib
import subprocess
from pathlib import Path
import sys
import tempfile

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from mcpblender.config import Config, write_config
from mcpblender.commands import DOMAIN_COMMANDS
from mcpblender.launcher import Launcher
from mcpblender.wire import call
from test_actions import recipe, recipe_file
from test_skeletal_animation import pytestmark
from test_shape_keys import make_flexible_limb


@pytest.fixture(scope="module")
def action_live():
    with tempfile.TemporaryDirectory(prefix="mb-action-") as directory:
        root = Path(directory)
        config = Config()
        config.bridge.socket_path = str(root / "bridge/bridge.sock")
        config.bridge.timeout_seconds = 90
        config.launch.background = True
        config.launch.auto_recover = False
        config.launch.log_path = str(root / "blender.log")
        config.stdio.library_root = str(root / "library")
        (root / "library").mkdir()
        config.stdio.allow_code_execution = False
        failing = recipe("custom.partial", [
            {"id": "create", "command": "create_primitive", "params": {"name": {"$input": "name"}}},
            {"id": "fail", "command": "inspect_mesh", "params": {"name": "MissingActionObject"}}])
        many = recipe("custom.many", [{"id": f"step{i}", "command": "create_primitive", "params": {"name": f"ActionPart{i}"}} for i in range(64)])
        many["inputs"] = {}
        config.action_files = [str(recipe_file(root / "actions.json", [recipe(), failing, many]))]
        path = root / "config.json"
        write_config(path, config)
        launcher = Launcher(config)
        try:
            launcher.start()
            yield root, path, config
        finally:
            if launcher.process and launcher.process.poll() is None:
                launcher.process.terminate()
                launcher.process.wait(timeout=15)
            call(config.bridge.socket_path, "shutdown", timeout=5)


def client_for(path):
    return Client(StdioTransport(command=sys.executable, args=["-m", "mcpblender.server", "--config", str(path)]), timeout=120)


def test_custom_action_execution_recovery_idempotency_and_partial_failure(action_live):
    _, path, _ = action_live

    async def run():
        async with client_for(path) as client:
            assert "execute_code" not in {t.name for t in await client.list_tools()}
            actions = (await client.call_tool("list_actions")).data["actions"]
            assert "custom.make_prop" in {a["name"] for a in actions}
            schema = (await client.call_tool("describe_action", {"action": "custom.make_prop"})).data["input_schema"]
            assert schema["properties"]["name"]["type"] == "string"
            planned = (await client.call_tool("plan_action", {"action": "custom.make_prop", "inputs": {"name": "ActionProp"}, "request_id": "prop-once"})).data
            result = (await client.call_tool("run_action", {"action": "custom.make_prop", "inputs": {"name": "ActionProp"}, "request_id": planned["request_id"], "wait": True})).data
            assert result["state"] == "succeeded", result
            assert result["result"]["steps"][1]["result"]["vertices"] == 8
            identifier = result["operation_id"]
            failed = (await client.call_tool("run_action", {"action": "custom.partial", "inputs": {"name": "ActionPartial"}, "wait": True})).data
            assert failed["state"] == "failed" and failed["result"]["steps"][0]["state"] == "succeeded"
            assert failed["result"]["steps"][1]["state"] == "failed"
            assert (await client.call_tool("get_object_info", {"name": "ActionPartial"})).data["name"] == "ActionPartial"
        async with client_for(path) as fresh:
            recovered = (await fresh.call_tool("run_action", {"action": "custom.make_prop", "inputs": {"name": "ActionProp"}, "request_id": "prop-once", "wait": True})).data
            assert recovered["operation_id"] == identifier and recovered["state"] == "succeeded"
    asyncio.run(run())


def test_action_cancellation_reports_completed_edits(action_live):
    _, path, _ = action_live

    async def run():
        async with client_for(path) as client:
            queued = (await client.call_tool("run_action", {"action": "custom.many"})).data
            for _ in range(200):
                operation = (await client.call_tool("get_operation", {"operation_id": queued["operation_id"]})).data
                if operation.get("progress", {}).get("completed_steps", 0) >= 1:
                    break
                await asyncio.sleep(.01)
            else:
                pytest.fail("Action did not complete its first step")
            await client.call_tool("cancel_operation", {"operation_id": queued["operation_id"]})
            result = (await client.call_tool("wait_operation", {"operation_id": queued["operation_id"], "timeout_seconds": 20})).data
            assert result["state"] == "cancelled", result
            done = [s for s in result["result"]["steps"] if s["state"] == "succeeded"]
            assert 1 <= len(done) < 64
            objects = (await client.call_tool("get_scene_info")).data["objects"]
            assert sum(o["name"].startswith("ActionPart") and o["name"][len("ActionPart"):].isdigit() for o in objects) == len(done)
    asyncio.run(run())


def test_preview_setup_fits_offset_assets_and_refuses_collisions(action_live):
    root, path, _ = action_live

    async def run():
        async with client_for(path) as client:
            async def tool(tool_name, **params):
                return (await client.call_tool(tool_name, {"params": params} if tool_name in DOMAIN_COMMANDS else params)).data
            await tool("create_primitive", name="StageAsset", location=[17, -8, 4], scale=[2, 1, 3])
            await tool("configure_material", name="StagePaint", base_color=[.08, .4, .65, 1], roughness=.45)
            await tool("assign_material", objects=["StageAsset"], material="StagePaint")
            await tool("configure_render", resolution_x=192, resolution_y=128, samples=4)
            before = await tool("get_scene_info")
            bad = await tool("run_action", action="preview_setup", inputs={"objects": ["StageAsset", "MissingStageMesh"]}, wait=True)
            assert bad["state"] == "failed"
            assert {o["name"] for o in (await tool("get_scene_info"))["objects"]} == {o["name"] for o in before["objects"]}
            result = await tool("run_action", action="preview_setup", inputs={"objects": ["StageAsset"]}, wait=True)
            assert result["state"] == "succeeded", result
            stage = result["result"]["steps"][0]["result"]
            assert stage["asset_center"] == pytest.approx([17, -8, 4])
            assert stage["camera"]["fully_in_frame"] and len(stage["lights"]) == 3
            assert all(abs(light["location"][0] - 17) < 20 for light in stage["lights"])
            snapshot = await tool("get_scene_info")
            collision = await tool("run_action", action="preview_setup", inputs={"objects": ["StageAsset"]}, wait=True)
            assert collision["state"] == "failed" and "already exists" in collision["error"]
            assert (await tool("get_scene_info"))["objects"] == snapshot["objects"]
            ortho = await tool("run_action", action="preview_setup", inputs={"objects": ["StageAsset"], "prefix": "Ortho", "projection": "ORTHO"}, wait=True)
            assert ortho["state"] == "succeeded" and ortho["result"]["steps"][0]["result"]["camera"]["fully_in_frame"]
            # Render from the perspective stage using the ordinary snapshot path.
            queued = await tool("submit_operation", command="render_still", params={"path": "stage.png", "width": 192, "height": 128, "engine": "CYCLES", "camera": "Preview_Camera"})
            rendered = await tool("wait_operation", operation_id=queued["operation_id"], timeout_seconds=60)
            assert rendered["state"] == "succeeded", rendered
            image = root / "library/stage.png"
            assert image.stat().st_size > 100
            if directory := os.environ.get("MCPBLENDER_ACTION_EVIDENCE_DIR"):
                destination = Path(directory)
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(image, destination / "stage.png")
    asyncio.run(run())


def test_game_asset_inspection_preflight_and_textured_delivery(action_live):
    root, path, config = action_live
    library = root / "library"
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    (library / "game-texture.png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x90\x20\x20\x90\xff" * 2)) + chunk(b"IEND", b""))

    async def run():
        async with client_for(path) as client:
            async def domain(command, **params):
                return (await client.call_tool(command, {"params": params})).data
            async def action(inputs):
                return (await client.call_tool("run_action", {"action": "game_asset_prepare", "inputs": inputs, "wait": True})).data
            await domain("create_mesh", name="GameBlock", vertices=[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], faces=[[0, 1, 2, 3]])
            await client.call_tool("set_object_transform", {"name": "GameBlock", "scale": [2, 3, 1]})
            before = (await client.call_tool("inspect_mesh", {"params": {"name": "GameBlock"}})).data
            rejected = await action({"objects": ["GameBlock", "MissingGameMesh"], "apply_rotation_scale": True})
            assert rejected["state"] == "failed"
            assert (await client.call_tool("inspect_mesh", {"params": {"name": "GameBlock"}})).data["geometry_sha256"] == before["geometry_sha256"]
            assert (await client.call_tool("get_object_info", {"name": "GameBlock"})).data["scale"] == [2, 3, 1]
            await domain("configure_material", name="GamePaint", image_path="game-texture.png")
            await domain("assign_material", objects=["GameBlock"], material="GamePaint")
            await client.call_tool("select_objects", {"names": ["Cube"]})
            selected_before = (await client.call_tool("get_scene_info")).data
            result = await action({"objects": ["GameBlock"], "apply_rotation_scale": True,
                "generate_uv": True, "triangulate": True, "save_path": "game.blend", "export_path": "game.glb"})
            assert result["state"] == "succeeded", result
            report = next(s["result"] for s in result["result"]["steps"] if s["id"] == "report")
            assert report["total_triangles"] == 2 and not report["errors"]
            assert {i["code"] for i in report["warnings"]} == {"non_manifold"}
            assert (await client.call_tool("get_object_info", {"name": "GameBlock"})).data["scale"] == [1, 1, 1]
            after = (await client.call_tool("get_scene_info")).data
            assert after["active_object"] == selected_before["active_object"]
            assert {o["name"] for o in after["objects"] if o["selected"]} == {"Cube"}
            exported = result["result"]["steps"][-1]["result"]
            assert exported["artifact_id"] and (library / "game.blend").is_file()
            data = (library / "game.glb").read_bytes()
            length = struct.unpack_from("<I", data, 12)[0]
            gltf = json.loads(data[20:20 + length])
            assert len(gltf["meshes"]) == 1 and gltf["meshes"][0]["name"].startswith("GameBlock")
            assert gltf["images"][0]["bufferView"] >= 0
            # Existing UVs are protected, before earlier requested transforms can run.
            protected = await action({"objects": ["GameBlock"], "generate_uv": True})
            assert protected["state"] == "failed" and "overwrite_uv" in protected["error"]

        readonly = config.model_copy(deep=True)
        readonly.stdio.allow_scene_edit = False
        readonly.stdio.allow_library_write = False
        readonly_path = root / "readonly.json"
        write_config(readonly_path, readonly)
        async with client_for(readonly_path) as client:
            checkpoint = (await client.call_tool("bridge_status")).data["bridge"].get("checkpoint_path")
            inspected = (await client.call_tool("run_action", {"action": "game_asset_prepare", "inputs": {"objects": ["GameBlock"], "triangle_budget": 1}, "wait": True})).data
            assert inspected["state"] == "succeeded", inspected
            assert "recovery" not in inspected["result"]
            assert any(i["code"] == "triangle_budget" for i in inspected["result"]["steps"][-1]["result"]["warnings"])
            assert (await client.call_tool("bridge_status")).data["bridge"].get("checkpoint_path") == checkpoint
    asyncio.run(run())


def test_animation_preview_combined_clips_video_and_restoration(action_live):
    root, path, _ = action_live
    library = root / "library"

    async def run():
        async with client_for(path) as client:
            async def domain(command, **params):
                return (await client.call_tool(command, {"params": params})).data
            async def snapshot():
                scene = (await client.call_tool("get_scene_info")).data
                scene.pop("filepath")
                resources = await domain("inspect_resources")
                resources.pop("dirty")
                resources.pop("filepath")
                state = {"mesh": (await domain("inspect_mesh", name="PreviewLimb"))["geometry_sha256"],
                        "morph": await domain("inspect_morph_animation", name="PreviewLimb"),
                        "rig": await domain("inspect_animation", name="PreviewRig"),
                        "scene": scene, "resources": resources}
                for value in state.values():
                    if isinstance(value, dict):
                        value.pop("operation_id", None)
                return state
            await make_flexible_limb(client, "PreviewRig", "PreviewLimb", "PreviewFlex", x=55)
            await domain("generate_uv", name="PreviewLimb")
            # Each test can run in isolation: generate a tiny texture here too.
            def chunk(kind, data):
                return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
            (library / "preview-texture.png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x90\x20\x20\x90\xff" * 2)) + chunk(b"IEND", b""))
            await domain("configure_material", name="PreviewTexture", image_path="preview-texture.png")
            await domain("assign_material", objects=["PreviewLimb"], material="PreviewTexture")
            await domain("set_active_clip", name="PreviewRig")
            await domain("set_active_morph_clip", name="PreviewLimb")
            await client.call_tool("set_frame", {"frame": 3})
            await domain("configure_render", resolution_x=128, resolution_y=128, fps=12, samples=4)
            stage = (await client.call_tool("run_action", {"action": "preview_setup", "inputs": {"objects": ["PreviewLimb"], "prefix": "Animation", "margin": 1.8}, "wait": True})).data
            assert stage["state"] == "succeeded", stage
            await domain("vse_add_strip", name="UserTitle", kind="TEXT", text="Keep my timeline", duration=10)
            original = await snapshot()
            inputs = {"frame_start": 1, "frame_end": 5, "width": 128, "height": 128,
                      "engine": "CYCLES", "camera": "Animation_Camera", "directory": "animation-preview",
                      "clips": [{"kind": "SKELETAL", "name": "PreviewRig", "clip": "PreviewFlex"},
                                {"kind": "MORPH", "name": "PreviewLimb", "clip": "PreviewFlex"}]}
            result = (await client.call_tool("run_action", {"action": "animation_preview", "inputs": inputs, "wait": True})).data
            assert result["state"] == "succeeded", result
            value = result["result"]["steps"][0]["result"]
            assert value["kind"] == "video" and value["restored_state"]
            assert len(value["preview_artifacts"]) == 3
            assert (library / "animation-preview/frame-000001.png").read_bytes() != (library / "animation-preview/frame-000005.png").read_bytes()
            probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", value["path"]]))
            stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
            assert stream["codec_name"] == "h264" and int(stream["nb_read_frames"]) == 5
            assert stream["r_frame_rate"] == "12/1"
            assert await snapshot() == original
            # Save/reopen and independently parse GLB delivered through the structured shim.
            await client.call_tool("save_blend", {"path": "preview-delivery.blend"})
            await client.call_tool("open_blend", {"path": "preview-delivery.blend"})
            reopened = await snapshot()
            for key in ("mesh", "morph", "rig", "scene"):
                assert reopened[key] == original[key]
            assert reopened["resources"]["render"] == original["resources"]["render"]
            assert reopened["resources"]["strips"] == original["resources"]["strips"]
            assert next(i for i in reopened["resources"]["images"] if i["name"] == "preview-texture.png")["packed"]
            await domain("export_objects_glb", objects=["PreviewRig", "PreviewLimb"], path="preview-delivery.glb")
            data = (library / "preview-delivery.glb").read_bytes()
            gltf = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
            assert {"rotation", "weights"} <= {c["target"]["path"] for a in gltf["animations"] for c in a["channels"]}
            assert gltf["images"][0]["bufferView"] >= 0
            if directory := os.environ.get("MCPBLENDER_ACTION_EVIDENCE_DIR"):
                destination = Path(directory)
                destination.mkdir(parents=True, exist_ok=True)
                for filename in ("frame-000001.png", "frame-000005.png", "preview.mp4"):
                    shutil.copy2(library / "animation-preview" / filename, destination / filename)
                (destination / "animation-evidence.json").write_text(json.dumps({"operation": result, "ffprobe": probe}, indent=2))

            restored = await snapshot()
            for mode in ("cancel", "encoding-failure"):
                directory = library / mode
                queued = (await client.call_tool("run_action", {"action": "animation_preview", "inputs": inputs | {
                    "directory": mode, "engine": "BLENDER_WORKBENCH", "width": 32, "height": 32, "frame_end": 30}})).data
                for _ in range(500):
                    status = (await client.call_tool("get_operation", {"operation_id": queued["operation_id"]})).data
                    nested = status.get("progress", {}).get("step_progress") or {}
                    if nested.get("completed_frames", 0) >= 1:
                        break
                    assert status["state"] not in {"failed", "succeeded"}, status
                    await asyncio.sleep(.01)
                else:
                    pytest.fail("Preview did not render its first frame")
                if mode == "cancel":
                    await client.call_tool("cancel_operation", {"operation_id": queued["operation_id"]})
                else:
                    # Force a late failure, after clip assignment and frame output.
                    (directory / "preview.mp4").mkdir()
                for _ in range(5):
                    finished = (await client.call_tool("wait_operation", {"operation_id": queued["operation_id"], "timeout_seconds": 60})).data
                    if finished["state"] in {"cancelled", "failed", "succeeded"}:
                        break
                assert finished["state"] == ("cancelled" if mode == "cancel" else "failed"), finished
                partial = finished["result"]["steps"][0]["partial_output"]
                assert partial["existing_frame_count"] >= 1 and partial["preview_artifacts"]
                assert await snapshot() == restored
    asyncio.run(run())

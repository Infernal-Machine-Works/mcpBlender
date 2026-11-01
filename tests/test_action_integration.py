"""Named action execution in disposable Blender with Python execution disabled."""
import asyncio
import os
import shutil
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

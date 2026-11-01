"""Named action execution in disposable Blender with Python execution disabled."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from mcpblender.config import Config, write_config
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

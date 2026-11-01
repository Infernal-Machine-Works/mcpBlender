import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from mcpblender.broker import Broker
from mcpblender.commands import Animation, Mesh, Video
from mcpblender.config import AssetSource, Config
from mcpblender.server import build_server


def attached(tmp_path, pid=None):
    broker = Broker(tmp_path / "bridge.sock")
    broker.request("register_worker", {"status": {"session_id": "test-session", "pid": pid or os.getpid()}})
    return broker


def submit(broker, owner="agent", request_id="edit", code="result = 1"):
    return broker.request("submit", {"owner": owner, "request_id": request_id,
                                     "command": "execute_code", "params": {"code": code}})


def test_idempotency_concurrency_ownership_and_restart(tmp_path):
    broker = attached(tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            submitted = list(pool.map(lambda _: submit(broker), range(24)))
        identifier = submitted[0]["operation_id"]
        assert {operation["operation_id"] for operation in submitted} == {identifier}
        with pytest.raises(ValueError, match="different arguments"):
            submit(broker, code="result = 2")
        with pytest.raises(ValueError, match="not found"):
            broker.request("get_operation", {"operation_id": identifier, "owner": "other"})
        work = broker.request("claim", {"session_id": "test-session"})["operation"]
        assert work["operation_id"] == identifier
        broker.journal.db.close()
        broker = Broker(tmp_path / "bridge.sock")
        # A broker restart must not claim and execute an already-running mutation again.
        assert broker.request("claim", {"session_id": "test-session"})["operation"] is None
        broker.request("complete", {"session_id": "test-session", "operation_id": identifier,
                                    "state": "succeeded", "result": {"success": True, "result": 1}})
        assert submit(broker)["result"]["result"] == 1
        assert broker.request("list_operations", {"owner": "agent"})["operations"][0]["state"] == "succeeded"
    finally:
        broker.journal.db.close()


def test_queued_cancel_and_running_cancel_are_distinct(tmp_path):
    broker = attached(tmp_path)
    try:
        first = submit(broker)
        second = submit(broker, request_id="second")
        broker.request("claim", {"session_id": "test-session"})
        cancelled = broker.request("cancel_operation", {"operation_id": second["operation_id"], "owner": "agent"})
        assert cancelled["state"] == "cancelled"
        running = broker.request("cancel_operation", {"operation_id": first["operation_id"], "owner": "agent"})
        assert running["state"] == "running" and running["cancel_requested"]
        reply = broker.request("progress", {"session_id": "test-session", "operation_id": first["operation_id"], "progress": {"fraction": 0.5}})
        assert reply["cancel_requested"]
    finally:
        broker.journal.db.close()


def test_worker_exit_marks_uncertain_and_never_replays(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    broker = attached(tmp_path, child.pid)
    try:
        operation = submit(broker)
        broker.request("claim", {"session_id": "test-session"})
        child.terminate()
        child.wait(timeout=5)
        status = broker.request("get_operation", {"operation_id": operation["operation_id"], "owner": "agent"})
        assert status["state"] == "interrupted" and status["outcome_unknown"]
        broker.request("register_worker", {"status": {"session_id": "new-session", "pid": os.getpid()}})
        assert broker.request("claim", {"session_id": "new-session"})["operation"] is None
        assert submit(broker)["state"] == "interrupted"
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        broker.journal.db.close()


def test_structured_contracts_reject_invalid_inputs():
    with pytest.raises(ValueError):
        Animation(frame_start=10, frame_end=1)
    with pytest.raises(ValueError):
        Video(frame_start=1, frame_end=10, width=127)
    with pytest.raises(ValueError):
        Mesh(name="invalid", vertices=[[0, 0, 0]] * 3, faces=[[0, 1, 99]])


def test_asset_origin_path_and_capability_checks(tmp_path):
    config = Config()
    config.bridge.socket_path = str(tmp_path / "private/bridge.sock")
    config.stdio.library_root = str(tmp_path / "library")
    config.asset_sources["generator"] = AssetSource(base_url="https://example.com/api/", headers={"Authorization": "secret"})
    _, state = build_server(config)
    prepared = state.prepare("download_asset", {"source": "generator", "resource": "output/model.glb", "path": "model.glb"})
    assert prepared["url"] == "https://example.com/api/output/model.glb"
    for resource in ("https://other.example/model.glb", "//other.example/model.glb", "file:///etc/passwd"):
        with pytest.raises(ValueError, match="origin"):
            state.prepare("download_asset", {"source": "generator", "resource": resource, "path": "model.glb"})
    config.stdio.allow_external_assets = False
    assert not state.allowed("download_asset")

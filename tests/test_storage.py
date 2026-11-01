from collections import namedtuple

import pytest

from mcpblender.broker import Broker
from mcpblender.storage import estimate, require_space, snapshot


def test_storage_admission_and_live_operation_reporting(tmp_path, monkeypatch):
    import os
    import mcpblender.storage as storage
    usage = namedtuple("Usage", "total used free")
    free = [1024**3]
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: usage(10 * 1024**3, 10 * 1024**3 - free[0], free[0]))
    broker = Broker(tmp_path / "bridge.sock")
    try:
        broker.request("register_worker", {"status": {"session_id": "storage-test", "pid": os.getpid()}})
        policy = {"path": str(tmp_path), "reserve_bytes": 256 * 1024**2, "estimated_bytes": 100 * 1024**2,
                  "estimate_kind": "test_estimate"}
        operation = broker.request("submit", {"owner": "agent", "command": "execute_code", "params": {"code": "result=1"}, "storage": policy})
        assert operation["storage"]["free_bytes"] == free[0]
        free[0] = 300 * 1024**2
        current = broker.request("get_operation", {"owner": "agent", "operation_id": operation["operation_id"]})
        assert current["storage"]["free_bytes"] == free[0]
        with pytest.raises(OSError, match="Insufficient storage"):
            broker.request("submit", {"owner": "agent", "command": "execute_code", "params": {"code": "result=2"}, "storage": policy})
        # Recheck when queued work is claimed, since another process can consume space.
        assert broker.request("claim", {"session_id": "storage-test"})["operation"] is None
        assert broker.request("get_operation", {"owner": "agent", "operation_id": operation["operation_id"]})["state"] == "failed"
    finally:
        broker.journal.db.close()


def test_sequence_estimate_scales_with_frames_and_resolution(tmp_path):
    one, _ = estimate("render_animation", {"width": 320, "height": 200, "frame_start": 1, "frame_end": 1})
    many, kind = estimate("render_animation", {"width": 320, "height": 200, "frame_start": 1, "frame_end": 100})
    assert many == one * 100 and kind == "conservative_pixel_bound"
    report = snapshot(tmp_path / "not-created/output")
    assert report["free_bytes"] > 0
    assert not (tmp_path / "not-created").exists()

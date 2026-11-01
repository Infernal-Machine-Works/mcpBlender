import asyncio
import json
import os
from pathlib import Path
import time

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from mcpblender.broker import Broker
from mcpblender.config import Config, Retention
from mcpblender.persistence import Records
from mcpblender.server import build_server


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    broker = Broker(tmp_path / "bridge.sock")
    broker.request("register_worker", {"status": {"session_id": "retention-test", "pid": os.getpid()}})
    policy = Retention(min_age_seconds=0, max_age_seconds=0, history_max_age_seconds=0).model_dump()
    yield broker, root, policy
    broker.retention.close()
    broker.journal.db.close()


def submit(broker, root, name, kind="file", owner="agent", explicit=False):
    path = root / (name + (".png" if kind == "file" else ""))
    spec = {"path": str(path), "kind": kind}
    if kind == "sequence":
        spec.update(frame_start=1, frame_end=3, frame_step=1, video=True)
    args = {"owner": owner, "request_id": name, "command": "render_still", "library_root": str(root),
            "params": {"path": str(path), "_temporary_outputs": [] if explicit else [spec]}}
    operation = broker.request("submit", args)
    return operation, path, args


def complete(broker, op, path, owner="agent", state="succeeded"):
    claimed = broker.request("claim", {"session_id": "retention-test"})["operation"]
    assert claimed["operation_id"] == op["operation_id"]
    if path.suffix:
        path.write_bytes(b"temporary pixels")
    else:
        path.mkdir()
        for name in ("frame-000001.png", "frame-000002.png", "manifest.json", "preview.mp4"):
            (path / name).write_bytes(b"temporary pixels")
    return broker.request("complete", {"session_id": "retention-test", "operation_id": op["operation_id"],
        "state": state, "result": {"path": str(path), "large": "payload" * 100}})


def test_cleanup_scope_pins_identity_and_tombstone_dedup(setup):
    broker, root, policy = setup
    op, output, request = submit(broker, root, "expired")
    complete(broker, op, output)
    explicit, saved, _ = submit(broker, root, "deliverable", explicit=True)
    complete(broker, explicit, saved)
    pinned, protected, _ = submit(broker, root, "pinned")
    complete(broker, pinned, protected)
    broker.retention.pin("agent", pinned["operation_id"], True)
    status = broker.retention.status("agent", str(root), policy, limit=1)
    assert len(status["output_groups"]) == 1 and status["next_offset"] == 1
    assert broker.retention.status("agent", str(root), policy)["pinned_output_groups"] == 1
    other, foreign, _ = submit(broker, root, "other", owner="another")
    complete(broker, other, foreign)
    plan = broker.retention.plan("agent", root, policy)
    assert [f["path"] for f in plan["files"]] == [str(output)]
    with pytest.raises(ValueError):
        broker.retention.apply("another", root, policy, plan["plan_id"])
    result = broker.retention.apply("agent", root, policy, plan["plan_id"])
    assert result["deleted_logical_bytes"] == len(b"temporary pixels")
    assert not output.exists() and saved.exists() and protected.exists() and foreign.exists()
    replay = broker.request("submit", request)
    assert replay["operation_id"] == op["operation_id"] and replay["details_pruned"] and replay["state"] == "succeeded"
    with pytest.raises(ValueError, match="different arguments"):
        broker.request("submit", request | {"params": {"changed": True}})


def test_plan_rechecks_pin_modifications_and_symlinks(setup, tmp_path):
    broker, root, policy = setup
    operations = []
    for name in ("pin-later", "modify-later", "symlink-later"):
        op, path, _ = submit(broker, root, name)
        complete(broker, op, path)
        operations.append((op, path))
    plan = broker.retention.plan("agent", root, policy)
    broker.retention.pin("agent", operations[0][0]["operation_id"], True)
    operations[1][1].write_bytes(b"user edit after preview")
    outside = tmp_path / "keep.txt"
    outside.write_text("keep outside")
    operations[2][1].unlink()
    operations[2][1].symlink_to(outside)
    result = broker.retention.apply("agent", root, policy, plan["plan_id"])
    assert not result["deleted_files"] and len(result["skipped_files"]) == 3
    assert outside.read_text() == "keep outside" and operations[1][1].read_bytes() == b"user edit after preview"


def test_sequence_preserves_unknown_files_inputs_and_active_unknown_operations(setup):
    broker, root, policy = setup
    op, directory, _ = submit(broker, root, "sequence", kind="sequence")
    complete(broker, op, directory)
    (directory / "user-notes.txt").write_text("keep")
    plan = broker.retention.plan("agent", root, policy)
    result = broker.retention.apply("agent", root, policy, plan["plan_id"])
    assert len(result["deleted_files"]) == 4
    assert (directory / "user-notes.txt").read_text() == "keep"
    op2, input_path, _ = submit(broker, root, "used-as-input")
    complete(broker, op2, input_path)
    active, active_path, active_args = submit(broker, root, "running")
    active_path.write_bytes(b"in progress")
    broker.request("submit", {"owner": "agent", "request_id": "use-image", "command": "configure_material",
        "params": {}, "library_root": str(root), "input_paths": [str(input_path)]})
    assert not broker.retention.plan("agent", root, policy)["files"]
    broker.journal.db.execute("UPDATE operations SET state='interrupted' WHERE id=?", (active["operation_id"],))
    broker.journal.db.commit()
    assert not broker.retention.plan("agent", root, policy)["files"]


def test_quota_minimum_age_scan_limit_and_root_scoping(setup, tmp_path):
    broker, root, policy = setup
    for name in ("one", "two"):
        op, path, _ = submit(broker, root, name)
        complete(broker, op, path)
    quota = policy | {"max_age_seconds": 86400, "max_temporary_bytes": 20}
    plan = broker.retention.plan("agent", root, quota)
    assert len(plan["files"]) == 1
    assert not broker.retention.plan("agent", root, quota | {"min_age_seconds": 3600})["files"]
    assert broker.retention.plan("agent", root, policy | {"max_scan_files": 1})["scan_truncated"]
    alternate = tmp_path / "other-library"
    alternate.mkdir()
    assert not broker.retention.plan("agent", alternate, policy)["history_operations"]


def test_automatic_maintenance_and_expired_missing_artifact_records(setup):
    broker, root, policy = setup
    op, path, _ = submit(broker, root, "automatic")
    complete(broker, op, path)
    assert broker.retention.automatic("agent", str(root), policy)["scheduled"]
    broker.retention.close()
    assert not path.exists()
    assert not broker.retention.automatic("agent", str(root), policy)["scheduled"]
    records = Records(root.parent / "private-records")
    records["missing"] = {"owner": "agent", "path": str(path), "expires": 0}
    records["foreign"] = {"owner": "another", "path": str(path), "expires": 0}
    assert records.prune_missing("agent") == 1 and "foreign" in records


def test_generated_outputs_and_retention_permissions(tmp_path):
    config = Config()
    config.bridge.socket_path = str(tmp_path / "private/bridge.sock")
    config.stdio.library_root = str(tmp_path / "library")
    config.stdio.allow_library_write = False
    server, state = build_server(config)
    auto = state.prepare("render_still", {}, "stable")
    assert auto["_temporary_outputs"][0]["path"] == auto["path"]
    assert auto == state.prepare("render_still", {}, "stable")
    assert "_temporary_outputs" not in state.prepare("render_still", {"path": "deliver.png"}, "explicit")
    async def run():
        async with Client(server) as client:
            names = {t.name for t in await client.list_tools()}
            assert {"retention_status", "preview_retention_cleanup"} <= names
            assert not {"apply_retention_cleanup", "set_operation_retention"} & names
            with pytest.raises(ToolError):
                await client.call_tool("apply_retention_cleanup", {"plan_id": "no"})
    asyncio.run(run())


def test_rejected_registration_leaves_no_queued_operation(setup):
    broker, root, policy = setup
    with pytest.raises(ValueError):
        broker.request("submit", {"owner": "agent", "request_id": "bad", "command": "create_primitive",
            "params": {"name": "NeverCreated", "_temporary_outputs": [{"kind": "file", "path": str(root.parent / "outside.png")}]},
            "library_root": str(root)})
    assert broker.request("claim", {"session_id": "retention-test"})["operation"] is None
    assert broker.journal.db.execute("SELECT count(*) FROM operations WHERE request_id='bad'").fetchone()[0] == 0


def test_sequence_directory_symlink_replacement_cannot_escape_library(setup, tmp_path):
    broker, root, policy = setup
    op, directory, _ = submit(broker, root, "directory-link", kind="sequence")
    complete(broker, op, directory)
    plan = broker.retention.plan("agent", root, policy)
    directory.rename(root / "kept-directory")
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    external = outside / "frame-000001.png"
    external.write_bytes(b"outside file")
    directory.symlink_to(outside, target_is_directory=True)
    result = broker.retention.apply("agent", root, policy, plan["plan_id"])
    assert not result["deleted_files"] and external.read_bytes() == b"outside file"

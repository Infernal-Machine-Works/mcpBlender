"""Bounded read-only startup retry must not hide protocol errors or spawn twice."""
import errno
import fcntl
from types import SimpleNamespace

import pytest

from mcpblender import broker
from mcpblender import launcher
from mcpblender.config import Config


@pytest.mark.parametrize("transient", ["Bridge timed out; execution may still be running.",
    "Bridge disconnected before a complete response"])
def test_startup_retries_transient_status_without_respawn(tmp_path, monkeypatch, transient):
    replies = [FileNotFoundError(errno.ENOENT, "not listening"), RuntimeError(transient), {"connected": False}]
    calls, processes = [], []
    def probe(path, command, **kwargs):
        calls.append(command)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    def spawn(*args, **kwargs):
        processes.append(args)
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(broker, "call", probe)
    monkeypatch.setattr(broker.subprocess, "Popen", spawn)
    monkeypatch.setattr(broker.time, "sleep", lambda delay: None)
    assert broker.ensure_broker(tmp_path / "private/bridge.sock") == {"connected": False}
    assert calls == ["status"] * 3 and len(processes) == 1


def test_startup_does_not_retry_protocol_or_policy_errors(tmp_path, monkeypatch):
    replies = [FileNotFoundError(errno.ENOENT, "not listening"), RuntimeError("Unsafe socket owner")]
    def probe(*args, **kwargs):
        raise replies.pop(0)
    monkeypatch.setattr(broker, "call", probe)
    monkeypatch.setattr(broker.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(poll=lambda: None))
    with pytest.raises(RuntimeError, match="Unsafe socket owner"):
        broker.ensure_broker(tmp_path / "private/bridge.sock")
    assert not replies


def test_frontend_attaches_to_recovery_pid_before_worker_registration(tmp_path, monkeypatch):
    config = Config()
    config.bridge.socket_path = str(tmp_path / "private/bridge.sock")
    pending = {"connected": False, "recovery": {"last_pid": 4242}}
    replies = [pending, {"connected": True, "pid": 4242}]
    monkeypatch.setattr(launcher, "ensure_broker", lambda *a, **kw: pending)
    monkeypatch.setattr(launcher, "process_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(launcher, "call", lambda *a, **kw: replies.pop(0))
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    def refuse_spawn(*a, **kw):
        pytest.fail("Frontend launched a second Blender while recovery was starting")
    monkeypatch.setattr(launcher.subprocess, "Popen", refuse_spawn)
    result = launcher.Launcher(config).start()
    assert result["started"] is False and result["status"]["pid"] == 4242
    assert not replies


def test_supervisor_defers_to_frontend_launch_lock_without_deadlock(tmp_path, monkeypatch):
    instance = broker.Broker(tmp_path / "bridge.sock")
    instance.lifecycle = {"desired_running": True, "auto_recover": True,
                          "args": ["blender"], "log_path": str(tmp_path / "app.log")}
    spawned = []
    monkeypatch.setattr(broker.subprocess, "Popen", lambda *a, **kw:
                        spawned.append(a) or SimpleNamespace(pid=4242, poll=lambda: None))
    # One synchronous supervisor iteration per invocation makes contention deterministic.
    def once():
        waits = iter([False, True])
        instance.stop_event = SimpleNamespace(wait=lambda _: next(waits))
        instance.supervise()
    try:
        with open(tmp_path / "launch.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            once()
            assert not spawned and not instance.lifecycle.get("error")
            assert instance.request("status", {})["connected"] is False
        once()
        assert len(spawned) == 1
        assert instance.request("status", {})["recovery"]["last_pid"] == 4242
    finally:
        instance.journal.db.close()

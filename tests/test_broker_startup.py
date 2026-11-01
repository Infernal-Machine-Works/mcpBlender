"""Bounded read-only startup retry must not hide protocol errors or spawn twice."""
import errno
from types import SimpleNamespace

import pytest

from mcpblender import broker


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

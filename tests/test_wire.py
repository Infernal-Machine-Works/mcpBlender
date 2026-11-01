import os
import socket
import threading

import pytest

from mcpblender.wire import call, check_peer, encode, listen_private


def test_private_socket_round_trip_and_live_socket_preserved(tmp_path):
    directory = tmp_path / "private"
    path = directory / "bridge.sock"
    listener = listen_private(path)
    listener.settimeout(2)
    assert directory.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeError, match="already listening"):
        listen_private(path)

    def serve():
        # First connection is the live-socket probe.
        probe, _ = listener.accept()
        probe.close()
        client, _ = listener.accept()
        with client:
            check_peer(client)
            assert b'"command": "status"' in client.recv(4096)
            client.sendall(encode({"ok": True, "result": {"pid": os.getpid()}}))

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        assert call(path, "status", timeout=2) == {"pid": os.getpid()}
    finally:
        thread.join(3)
        listener.close()


def test_refuse_unsafe_parent_and_non_socket(tmp_path):
    directory = tmp_path / "public"
    directory.mkdir(mode=0o755)
    with pytest.raises(RuntimeError, match="0700"):
        listen_private(directory / "bridge.sock")
    directory.chmod(0o700)
    path = directory / "bridge.sock"
    path.write_text("preserve me")
    with pytest.raises(RuntimeError, match="Refusing"):
        listen_private(path)
    assert path.read_text() == "preserve me"


def test_replace_stale_socket(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "bridge.sock"
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(path))
    stale.close()
    listener = listen_private(path)
    listener.close()


def test_protocol_limit():
    with pytest.raises(ValueError, match="8 MiB"):
        encode({"data": "x" * (8 * 1024 * 1024)})

"""Private same-user Unix sockets and a bounded newline JSON protocol (stdlib only)."""
from __future__ import annotations

import ctypes
import errno
import json
import os
import socket
import stat
import struct
import sys
from pathlib import Path

MAX_MESSAGE = 8 * 1024 * 1024


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"Directory must be owned by this user with mode 0700: {path}")


def check_peer(sock: socket.socket) -> None:
    if sys.platform.startswith(("freebsd", "darwin", "openbsd", "netbsd")):
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.getpeereid(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise OSError(ctypes.get_errno(), "getpeereid failed")
        peer_uid = uid.value
    elif hasattr(socket, "SO_PEERCRED"):
        _, peer_uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    else:
        raise RuntimeError("Same-user Unix peer verification is unsupported on this platform")
    if peer_uid != os.getuid():
        raise PermissionError("Bridge peer must have the same UID")


def listen_private(path: Path) -> socket.socket:
    private_directory(path.parent)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError(f"Refusing to replace non-owned socket or other file: {path}")
        with socket.socket(socket.AF_UNIX) as probe:
            try:
                probe.connect(str(path))
            except OSError as exc:
                if exc.errno != errno.ECONNREFUSED:
                    raise
            else:
                raise RuntimeError(f"A bridge is already listening: {path}")
        path.unlink()
    listener = socket.socket(socket.AF_UNIX)
    try:
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(16)
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise


def encode(value: dict) -> bytes:
    data = json.dumps(value, allow_nan=False).encode() + b"\n"
    if len(data) > MAX_MESSAGE:
        raise ValueError("Bridge message exceeds 8 MiB")
    return data


def call(path: str | Path, command: str, params: dict | None = None, timeout: float = 120) -> dict:
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(str(Path(path).expanduser()))
            check_peer(sock)
            sock.sendall(encode({"command": command, "params": params or {}}))
            data = bytearray()
            while b"\n" not in data:
                chunk = sock.recv(65536)
                if not chunk:
                    raise RuntimeError("Bridge disconnected before a complete response")
                data.extend(chunk)
                if len(data) > MAX_MESSAGE:
                    raise RuntimeError("Bridge response exceeds 8 MiB")
        except TimeoutError as exc:
            raise RuntimeError("Bridge timed out; execution may still be running. Do not blindly retry mutations.") from exc
    response = json.loads(data.split(b"\n", 1)[0])
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "Bridge command failed"))
    return response["result"]

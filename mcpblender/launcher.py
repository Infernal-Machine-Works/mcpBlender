"""Explicit Blender startup, serialized across MCP server processes."""
from __future__ import annotations

import fcntl
import errno
import os
import socket
import subprocess
import sys
import threading
import time
import json
from pathlib import Path

from .config import Config
from .wire import call, private_directory
from .broker import ensure_broker, process_alive


class Launcher:
    def __init__(self, config: Config):
        self.config = config
        self.process: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.frontend_managed = False

    def _wait_for_pid(self, path, pid, started, log):
        deadline = time.monotonic() + self.config.launch.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.process and self.process.pid == pid:
                exited = self.process.poll() is not None
            else:
                exited = not process_alive(pid)
            if exited:
                raise RuntimeError(f"Blender exited during startup; inspect {log}")
            try:
                status = call(path, "status", timeout=0.5)
            except (OSError, RuntimeError):
                time.sleep(0.1)
                continue
            if not status.get("connected"):
                time.sleep(0.1)
                continue
            if status["pid"] != pid:
                raise RuntimeError("Socket belongs to a different Blender process")
            return {"started": started, "status": status, "log_path": str(log)}
        raise RuntimeError(f"Blender startup timed out; process may still start. Inspect {log}")

    def shutdown(self):
        """Opt-in frontend-owned shutdown: preserve a checkpoint before quitting."""
        path = self.config.bridge.socket_path
        if not call(path, "status")["connected"]:
            return
        for command, params in (("checkpoint", {}), ("shutdown_blender", {"discard_unsaved": True})):
            operation = call(path, "submit", {"owner": "lifecycle", "command": command, "params": params, "request_id": os.urandom(16).hex()})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                result = call(path, "get_operation", {"operation_id": operation["operation_id"], "owner": "lifecycle"})
                if result["state"] in ("succeeded", "failed", "cancelled", "interrupted"):
                    if result["state"] != "succeeded":
                        raise RuntimeError("Automatic shutdown stopped because recovery checkpoint or graceful quit failed")
                    break
                time.sleep(.1)
            else:
                raise RuntimeError("Automatic shutdown is still queued/running; Blender was not forcibly stopped")

    def start(self, background: bool | None = None, recover: bool = True) -> dict:
        with self.lock:
            config = self.config
            path = Path(config.bridge.socket_path).expanduser()
            private_directory(path.parent)
            with open(path.parent / "launch.lock", "a") as lock:
                os.chmod(lock.name, 0o600)
                fcntl.flock(lock, fcntl.LOCK_EX)
                broker = ensure_broker(path, python=sys.executable)
                if broker["connected"]:
                    return {"started": False, "status": broker}
                log = Path(config.launch.log_path).expanduser()
                # Recovery can have spawned Blender before it registers. All
                # launchers share this lock with the supervisor; wait for that
                # live PID instead of creating another application instance.
                pending_pid = broker.get("recovery", {}).get("last_pid")
                if pending_pid and process_alive(pending_pid):
                    return self._wait_for_pid(path, pending_pid, False, log)
                if self.process and self.process.poll() is None:
                    raise RuntimeError("Previously launched Blender is still starting; inspect its log")
                log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                args = [config.launch.executable, "--disable-autoexec"]
                use_background = config.launch.background if background is None else background
                if use_background:
                    args.append("--background")
                if config.launch.factory_startup:
                    args.append("--factory-startup")
                args += ["--python-exit-code", "1", "--python",
                         str(Path(__file__).parent / "blender/bootstrap.py")]
                bridge_env = dict(MCPBLENDER_BRIDGE_SOCKET=str(path), MCPBLENDER_PYTHON=sys.executable,
                                  MCPBLENDER_CHECKPOINT_INTERVAL=str(config.launch.checkpoint_interval_seconds),
                                  MCPBLENDER_CHECKPOINT_COPIES=str(config.launch.checkpoint_copies),
                                  MCPBLENDER_RESERVE_BYTES=str(config.storage.reserve_bytes))
                env = dict(os.environ, **bridge_env)
                env.pop("MCPBLENDER_RECOVER", None)
                manifest = path.parent / "recovery/latest.json"
                if recover and manifest.is_file():
                    checkpoint = json.loads(manifest.read_text())["checkpoint_path"]
                    if Path(checkpoint).is_file():
                        env["MCPBLENDER_RECOVER"] = checkpoint
                fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "ab") as stream:
                    self.process = subprocess.Popen(args, stdout=stream, stderr=stream, env=env,
                                                    stdin=subprocess.DEVNULL, start_new_session=True)
                call(path, "configure_lifecycle", {"options": {
                    "args": args, "bridge_env": bridge_env, "log_path": str(log), "desired_running": True,
                    "auto_recover": config.launch.auto_recover, "max_recovery_attempts": config.launch.max_recovery_attempts,
                    "last_pid": self.process.pid, "recovery_attempts": 0,
                    "checkpoint_path": env.get("MCPBLENDER_RECOVER"),
                    "frontend_pid": os.getpid(),
                    "shutdown_on_exit": self.frontend_managed and config.launch.shutdown_on_exit,
                }}, timeout=5)
                return self._wait_for_pid(path, self.process.pid, True, log)

"""Durable operation queue, outside Blender's busy main thread (stdlib only).

All peers are the same OS user. Remote policy/ownership is enforced by the MCP
frontend, which supplies owner IDs; this local control protocol is not a sandbox.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .wire import MAX_MESSAGE, call, check_peer, encode, listen_private, private_directory
from .network import COMMANDS as NETWORK_COMMANDS, private_summary, transfer
from .storage import snapshot, require_space

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
_PROCESS_CACHE = {}


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    cached = _PROCESS_CACHE.get(pid)
    if cached and time.monotonic() - cached[0] < 0.5:
        return cached[1]
    # kill(pid, 0) also succeeds for zombies. An orphaned Blender can remain a
    # zombie until its launching frontend exits; it is no longer a live worker.
    status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    alive = not (status.returncode == 0 and status.stdout.strip().startswith("Z"))
    _PROCESS_CACHE[pid] = time.monotonic(), alive
    return alive


class Journal:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                digest TEXT NOT NULL, command TEXT NOT NULL, params TEXT NOT NULL,
                session_id TEXT NOT NULL, state TEXT NOT NULL,
                submitted_at REAL NOT NULL, started_at REAL, finished_at REAL,
                progress TEXT, result TEXT, error TEXT, cancel_requested INTEGER DEFAULT 0,
                UNIQUE(owner, request_id)
            );
            CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        if "storage" not in {row[1] for row in self.db.execute("PRAGMA table_info(operations)")}:
            self.db.execute("ALTER TABLE operations ADD COLUMN storage TEXT")
        if "claim_id" not in {row[1] for row in self.db.execute("PRAGMA table_info(operations)")}:
            self.db.execute("ALTER TABLE operations ADD COLUMN claim_id TEXT")
        self.db.commit()

    def row(self, identifier):
        row = self.db.execute("SELECT * FROM operations WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Operation not found")
        return dict(row)

    @staticmethod
    def public(row):
        result = {key: row[key] for key in ("owner", "command", "session_id", "state", "submitted_at",
                                           "started_at", "finished_at", "error", "request_id")}
        result.update(operation_id=row["id"], job_id=row["id"], cancel_requested=bool(row["cancel_requested"]))
        for key in ("progress", "result"):
            if row[key] is not None:
                result[key] = json.loads(row[key])
        result["outcome_unknown"] = row["state"] == "interrupted"
        if row.get("storage"):
            policy = json.loads(row["storage"])
            initial = policy.pop("estimated_bytes", 0)
            kind = policy.pop("estimate_kind", "unknown")
            result["storage"] = snapshot(**policy)
            result["storage"].update(initial_estimated_output_bytes=initial, initial_estimate_kind=kind)
        return result


class Broker:
    def __init__(self, path):
        self.path = Path(path)
        self.journal = Journal(self.path.parent / "operations.sqlite3")
        previous = self.journal.db.execute("SELECT value FROM metadata WHERE name='worker'").fetchone()
        self.worker = json.loads(previous[0]) if previous else None
        self.epoch = uuid.uuid4().hex
        self.shutdown = None
        row = self.journal.db.execute("SELECT value FROM metadata WHERE name='lifecycle'").fetchone()
        self.lifecycle = json.loads(row[0]) if row else {}
        self.stop_event = threading.Event()
        self.supervisor = None
        self.children = []
        self.journal.db.execute("UPDATE operations SET state='interrupted', finished_at=?, error=? "
                               "WHERE session_id='network' AND state IN ('queued','running')",
                               (time.time(), "Broker restarted during an external API operation; remote outcome may be unknown"))
        self.journal.db.commit()

    def persist_lifecycle(self):
        self.journal.db.execute("INSERT OR REPLACE INTO metadata VALUES ('lifecycle',?)", (json.dumps(self.lifecycle),))
        self.journal.db.commit()

    def supervise(self):
        while not self.stop_event.wait(.2):
            with self.journal.lock:
                try:
                    self.children = [child for child in self.children if child.poll() is None]
                    self.reconcile()
                    life = self.lifecycle
                    if life.get("desired_running") and life.get("shutdown_on_exit") and not process_alive(life["frontend_pid"]):
                        if not self.worker:
                            life["desired_running"] = False
                            self.persist_lifecycle()
                        elif not life.get("shutdown_requested"):
                            self.request("submit", {"owner": "lifecycle", "request_id": "frontend-exit-" + str(life["frontend_pid"]),
                                                    "command": "shutdown_blender", "params": {"discard_unsaved": True, "_checkpoint_before_exit": True}})
                            life["shutdown_requested"] = True
                            self.persist_lifecycle()
                    if self.worker or not life.get("desired_running") or not life.get("auto_recover"):
                        continue
                    if life.get("last_pid") and process_alive(life["last_pid"]):
                        continue
                    if life.get("recovery_attempts", 0) >= life.get("max_recovery_attempts", 3):
                        life["error"] = "Automatic recovery attempt limit reached; explicit start is required"
                        continue
                    env = dict(os.environ, **life.get("bridge_env", {}))
                    checkpoint = life.get("checkpoint_path")
                    if checkpoint and Path(checkpoint).is_file():
                        env["MCPBLENDER_RECOVER"] = checkpoint
                    else:
                        env.pop("MCPBLENDER_RECOVER", None)
                    fd = os.open(life["log_path"], os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
                    with os.fdopen(fd, "ab") as log:
                        child = subprocess.Popen(life["args"], env=env, stdin=subprocess.DEVNULL,
                                                 stdout=log, stderr=log, start_new_session=True)
                    self.children.append(child)
                    life.update(last_pid=child.pid, recovery_attempts=life.get("recovery_attempts", 0) + 1,
                                last_recovery_at=time.time(), recovered_from=checkpoint, error=None)
                    self.persist_lifecycle()
                except Exception as exc:
                    self.lifecycle["error"] = f"{type(exc).__name__}: application recovery failed"

    def start_supervisor(self):
        self.supervisor = threading.Thread(target=self.supervise, daemon=True)
        self.supervisor.start()

    def network_operation(self, identifier, command, params):
        def progress(value):
            with self.journal.lock:
                policy = params.get("_storage_policy", {})
                value = value | {"storage": snapshot(policy.get("path", self.path.parent), reserve_bytes=policy.get("reserve_bytes", 256 * 1024**2))}
                self.journal.db.execute("UPDATE operations SET progress=? WHERE id=?", (json.dumps(value), identifier))
                self.journal.db.commit()

        def cancelled():
            with self.journal.lock:
                return bool(self.journal.row(identifier)["cancel_requested"])

        result, error, state = None, None, "succeeded"
        try:
            result = transfer(command, params, progress, cancelled)
        except InterruptedError as exc:
            error, state = str(exc), "cancelled"
        except Exception as exc:
            # urllib errors may include signed URLs: record only the exception type.
            error, state = f"{type(exc).__name__}: external transfer failed", "failed"
        with self.journal.lock:
            self.journal.db.execute("UPDATE operations SET state=?, result=?, error=?, finished_at=? WHERE id=?",
                                    (state, json.dumps(result), error, time.time(), identifier))
            self.journal.db.commit()

    def reconcile(self):
        if self.worker and not process_alive(self.worker["pid"]):
            session = self.worker["session_id"]
            self.journal.db.execute(
                "UPDATE operations SET state='interrupted', finished_at=?, error=? "
                "WHERE session_id=? AND state NOT IN ('succeeded','failed','cancelled','interrupted')",
                (time.time(), "Blender process exited; partial scene edits or output files may remain", session))
            self.worker = None
            self.journal.db.execute("DELETE FROM metadata WHERE name='worker'")
            self.journal.db.commit()

    def worker_check(self, params):
        if not self.worker or params.get("session_id") != self.worker["session_id"]:
            raise ValueError("Worker registration required")

    def request(self, command, params):
        with self.journal.lock:
            self.reconcile()
            db = self.journal.db
            if command == "status":
                busy = db.execute("SELECT id FROM operations WHERE state='running' AND session_id!='network' ORDER BY submitted_at LIMIT 1").fetchone()
                return dict(self.worker or {}, protocol=2, connected=self.worker is not None,
                            broker_pid=os.getpid(), broker_epoch=self.epoch,
                            storage=snapshot(self.path.parent),
                            recovery={key: self.lifecycle.get(key) for key in ("auto_recover", "desired_running", "recovery_attempts", "checkpoint_path", "recovered_from", "error")},
                            active_operation=busy[0] if busy else None,
                            queued_operations=db.execute("SELECT count(*) FROM operations WHERE state='queued'").fetchone()[0])
            if command == "register_worker":
                status = params["status"]
                if self.worker and self.worker["session_id"] != status["session_id"]:
                    raise RuntimeError("A live Blender worker is already attached")
                db.execute("UPDATE operations SET state='interrupted', finished_at=?, error=? "
                           "WHERE session_id!=? AND session_id!='network' AND state IN ('queued','running')",
                           (time.time(), "Blender session changed; operation was not replayed", status["session_id"]))
                self.worker = status | {"last_contact": time.time()}
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('worker',?)", (json.dumps(self.worker),))
                db.commit()
                return {"registered": True, "broker_epoch": self.epoch}
            if command == "configure_lifecycle":
                self.lifecycle = params["options"]
                self.persist_lifecycle()
                return {"configured": True}
            if command == "desired_state":
                self.worker_check(params)
                self.lifecycle["desired_running"] = bool(params["running"])
                self.persist_lifecycle()
                return {"desired_running": bool(params["running"])}
            if command == "detach_worker":
                self.worker_check(params)
                db.execute("UPDATE operations SET state='interrupted', finished_at=?, error=? WHERE session_id=? AND state IN ('queued','running')",
                           (time.time(), "Blender bridge detached; partial edits may remain", self.worker["session_id"]))
                self.worker = None
                db.execute("DELETE FROM metadata WHERE name='worker'")
                db.commit()
                return {"detached": True}
            if command == "submit":
                owner = params["owner"]
                request_id = params.get("request_id") or uuid.uuid4().hex
                body = {"command": params["command"], "params": params.get("params", {})}
                encoded = json.dumps(body, sort_keys=True, allow_nan=False)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                previous = db.execute("SELECT * FROM operations WHERE owner=? AND request_id=?", (owner, request_id)).fetchone()
                if previous:
                    if previous["digest"] != digest:
                        raise ValueError("request_id was already used with different arguments")
                    return Journal.public(dict(previous))
                network = body["command"] in NETWORK_COMMANDS
                if not self.worker and not network:
                    raise RuntimeError("No Blender worker attached; start Blender explicitly")
                if db.execute("SELECT count(*) FROM operations WHERE state IN ('queued','running')").fetchone()[0] >= 128:
                    raise RuntimeError("Operation queue is full")
                identifier = uuid.uuid4().hex
                if params.get("storage"):
                    require_space(**params["storage"])
                db.execute("INSERT INTO operations (id,owner,request_id,digest,command,params,session_id,state,submitted_at) "
                           "VALUES (?,?,?,?,?,?,?,'queued',?)",
                           (identifier, owner, request_id, digest, body["command"],
                            json.dumps(private_summary(body["params"]) if network else body["params"]),
                            "network" if network else self.worker["session_id"], time.time()))
                if network:
                    db.execute("UPDATE operations SET params=?, state='running', started_at=? WHERE id=?",
                               (json.dumps(private_summary(body["params"])), time.time(), identifier))
                if params.get("storage"):
                    db.execute("UPDATE operations SET storage=? WHERE id=?", (json.dumps(params["storage"]), identifier))
                db.commit()
                if network:
                    runtime = body["params"] | {"_storage_policy": params.get("storage", {})}
                    threading.Thread(target=self.network_operation, args=(identifier, body["command"], runtime), daemon=True).start()
                return Journal.public(self.journal.row(identifier))
            if command in ("get_operation", "cancel_operation"):
                row = self.journal.row(params["operation_id"])
                if row["owner"] != params["owner"]:
                    raise ValueError("Operation not found")
                if command == "cancel_operation" and row["state"] not in TERMINAL:
                    db.execute("UPDATE operations SET cancel_requested=1 WHERE id=?", (row["id"],))
                    if row["state"] == "queued":
                        db.execute("UPDATE operations SET state='cancelled', finished_at=? WHERE id=?", (time.time(), row["id"]))
                    db.commit()
                    row = self.journal.row(row["id"])
                return Journal.public(row)
            if command == "list_operations":
                limit = min(200, max(1, int(params.get("limit", 30))))
                rows = db.execute("SELECT * FROM operations WHERE owner=? ORDER BY submitted_at DESC LIMIT ?", (params["owner"], limit))
                return {"operations": [Journal.public(dict(row)) for row in rows]}
            if command in ("claim", "progress", "complete"):
                self.worker_check(params)
                self.worker["last_contact"] = time.time()
                if params.get("status"):
                    self.worker.update(params["status"])
                    if params["status"].get("checkpoint_path"):
                        self.lifecycle["checkpoint_path"] = params["status"]["checkpoint_path"]
                        self.persist_lifecycle()
                if command == "claim":
                    busy = db.execute("SELECT * FROM operations WHERE state='running' AND session_id!='network'").fetchone()
                    if busy:
                        if params.get("claim_id") and busy["claim_id"] == params["claim_id"]:
                            return {"operation": {"operation_id": busy["id"], "command": busy["command"], "params": json.loads(busy["params"]),
                                                  "storage": json.loads(busy["storage"]) if busy["storage"] else {}}}
                        return {"operation": None}
                    row = db.execute("SELECT * FROM operations WHERE state='queued' AND session_id!='network' ORDER BY submitted_at,id LIMIT 1").fetchone()
                    if row is None:
                        return {"operation": None}
                    db.execute("UPDATE operations SET state='running', started_at=?, claim_id=? WHERE id=?", (time.time(), params.get("claim_id"), row["id"]))
                    db.commit()
                    if row["storage"]:
                        try:
                            require_space(**json.loads(row["storage"]))
                        except OSError as exc:
                            db.execute("UPDATE operations SET state='failed', error=?, finished_at=? WHERE id=?", (str(exc), time.time(), row["id"]))
                            db.commit()
                            return {"operation": None}
                    return {"operation": {"operation_id": row["id"], "command": row["command"], "params": json.loads(row["params"]),
                                          "storage": json.loads(row["storage"]) if row["storage"] else {}}}
                row = self.journal.row(params["operation_id"])
                if command == "complete" and row["session_id"] == self.worker["session_id"] and row["state"] in TERMINAL:
                    if row["state"] == params["state"] and json.loads(row["result"]) == params.get("result"):
                        return {"cancel_requested": bool(row["cancel_requested"])}
                if row["session_id"] != self.worker["session_id"] or row["state"] != "running":
                    raise ValueError("Operation does not belong to the active worker or is already terminal")
                if command == "progress":
                    db.execute("UPDATE operations SET progress=? WHERE id=?", (json.dumps(params.get("progress", {})), row["id"]))
                else:
                    state = params["state"]
                    if state not in TERMINAL:
                        raise ValueError("Invalid terminal state")
                    db.execute("UPDATE operations SET state=?, result=?, error=?, finished_at=? WHERE id=?",
                               (state, json.dumps(params.get("result")), params.get("error"), time.time(), row["id"]))
                    if row["owner"] == "lifecycle" and row["command"] == "shutdown_blender" and state != "succeeded":
                        self.lifecycle["error"] = "Automatic shutdown failed; application was left running to preserve edits"
                        self.persist_lifecycle()
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('worker',?)", (json.dumps(self.worker),))
                db.commit()
                return {"cancel_requested": bool(row["cancel_requested"])}
            if command in ("shutdown", "restart_broker"):
                if command == "shutdown" and (self.worker or db.execute("SELECT count(*) FROM operations WHERE session_id='network' AND state='running'").fetchone()[0]):
                    raise RuntimeError("Stop the test-owned Blender process before shutting down its broker")
                if self.shutdown:
                    threading.Thread(target=self.shutdown, daemon=True).start()
                return {"stopping": True}
            raise ValueError(f"Unknown broker command: {command}")


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            check_peer(self.request)
            data = bytearray()
            while b"\n" not in data:
                part = self.request.recv(65536)
                if not part:
                    return
                data.extend(part)
                if len(data) > MAX_MESSAGE:
                    raise ValueError("Request exceeds 8 MiB")
            message = json.loads(data.split(b"\n", 1)[0])
            response = {"ok": True, "result": self.server.broker.request(message["command"], message.get("params", {}))}
            encoded = encode(response)
        except Exception as exc:
            encoded = encode({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        try:
            self.request.sendall(encoded)
        except OSError:
            pass


class UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 32


def serve(path):
    os.umask(0o077)
    path = Path(path).expanduser()
    listener = listen_private(path)
    listener.setblocking(True)
    inode = path.stat().st_ino
    broker = Broker(path)
    with UnixServer(str(path), Handler, bind_and_activate=False) as server:
        server.socket.close()
        server.socket = listener
        server.broker = broker
        broker.shutdown = server.shutdown
        broker.start_supervisor()
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            broker.stop_event.set()
            broker.supervisor.join(timeout=2)
            broker.journal.db.close()
            if path.exists() and path.stat().st_ino == inode:
                path.unlink()


def ensure_broker(path, python=None):
    """Start the stdlib-only broker once; no bpy dependencies and no Blender threads."""
    path = Path(path).expanduser()
    private_directory(path.parent)
    with open(path.parent / "broker-start.lock", "a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return call(path, "status", timeout=1)
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED):
                raise
        import shutil
        executable = python or os.environ.get("MCPBLENDER_PYTHON") or shutil.which("python3")
        if not executable:
            raise RuntimeError("Python 3 is required for the operation broker")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
        log_path = path.parent / "broker.log"
        fd = os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as log:
            process = subprocess.Popen([executable, "-m", "mcpblender.broker", "--socket", str(path)],
                                       env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                return call(path, "status", timeout=0.5)
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError(f"Operation broker exited; inspect {log_path}")
                time.sleep(0.05)
        raise RuntimeError(f"Operation broker startup timed out; inspect {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    serve(parser.parse_args().socket)

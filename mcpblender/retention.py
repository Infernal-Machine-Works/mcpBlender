"""Conservative retention of registered temporary outputs; never a library sweep."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

from .storage import snapshot

TERMINAL = {"succeeded", "failed", "cancelled"}


@contextmanager
def parent_fd(root, path):
    """Anchor every path component, refusing symlinks (including directory links)."""
    root, path = Path(root), Path(path)
    relative = path.relative_to(root)
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise ValueError("Temporary output must be below library root")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root, flags)
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, relative.name
    finally:
        os.close(fd)


def fingerprint(info):
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size, "mtime_ns": info.st_mtime_ns}


class Retention:
    def __init__(self, journal):
        self.journal = journal
        self.busy = set()
        self.last_run = {}
        self.threads = []
        with journal.lock:
            journal.db.executescript('''
                CREATE TABLE IF NOT EXISTS temporary_outputs (
                    id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, owner TEXT NOT NULL,
                    library_root TEXT NOT NULL, spec TEXT NOT NULL, pinned INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS retention_plans (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, library_root TEXT NOT NULL,
                    expires REAL NOT NULL, plan TEXT NOT NULL
                );
            ''')
            if "details_pruned" not in {r[1] for r in journal.db.execute("PRAGMA table_info(operations)")}:
                journal.db.execute("ALTER TABLE operations ADD COLUMN details_pruned INTEGER DEFAULT 0")
            if "library_root" not in {r[1] for r in journal.db.execute("PRAGMA table_info(operations)")}:
                journal.db.execute("ALTER TABLE operations ADD COLUMN library_root TEXT")
            journal.db.executescript('''
                CREATE INDEX IF NOT EXISTS temporary_owner_root ON temporary_outputs(owner,library_root);
                CREATE INDEX IF NOT EXISTS operation_retention_age ON operations(owner,library_root,finished_at);
                CREATE INDEX IF NOT EXISTS retention_plan_expiry ON retention_plans(expires);
            ''')
            journal.db.commit()

    def register(self, operation_id, owner, library_root, specs, references):
        # Caller holds the journal lock. Registration precedes worker admission.
        root = str(Path(library_root).expanduser().resolve())
        db = self.journal.db
        db.execute("UPDATE operations SET library_root=? WHERE id=?", (root, operation_id))
        for spec in specs:
            path = Path(spec["path"])
            if spec["kind"] not in {"file", "sequence"} or not path.is_absolute() or not path.is_relative_to(root) or ".." in path.parts:
                raise ValueError("Invalid temporary-output registration")
            if spec["kind"] == "file" and path.suffix not in {".png", ".mp4"}:
                raise ValueError("Only generated render outputs can be temporary")
            # A pre-existing file/directory was not created by this operation.
            if path.exists() or path.is_symlink():
                continue
            identifier = hashlib.sha256((operation_id + str(path)).encode()).hexdigest()
            db.execute("INSERT OR IGNORE INTO temporary_outputs VALUES (?,?,?,?,?,0)",
                       (identifier, operation_id, owner, root, json.dumps(spec)))
        # Using an output as a structured input protects its whole output group.
        for row in db.execute("SELECT id,spec FROM temporary_outputs").fetchall() if references else []:
            spec = json.loads(row["spec"])
            path = Path(spec["path"])
            if any(Path(ref) == path or (spec["kind"] == "sequence" and Path(ref).is_relative_to(path)) for ref in references):
                db.execute("UPDATE temporary_outputs SET pinned=1 WHERE id=?", (row["id"],))

    def pin(self, owner, identifier, pinned):
        with self.journal.lock:
            operation = self.journal.row(identifier)
            if operation["owner"] != owner:
                raise ValueError("Operation not found")
            self.journal.db.execute("UPDATE temporary_outputs SET pinned=? WHERE owner=? AND operation_id=?", (int(pinned), owner, identifier))
            # Pins also protect explicit-output operation history.
            self.journal.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", ("retention-pin:" + identifier, json.dumps(bool(pinned))))
            self.journal.db.commit()
            return {"operation_id": identifier, "pinned": pinned}

    @staticmethod
    def names(spec):
        path = Path(spec["path"])
        if spec["kind"] == "file":
            yield path
            return
        # Only known output names are candidates. Unknown files/subdirectories stay.
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    name = entry.name
                    match = re.fullmatch(r"frame-(-?\d+)\.png", name)
                    known = name == "manifest.json" or (spec.get("video") and name == "preview.mp4")
                    if match:
                        frame = int(match[1])
                        known = (name == f"frame-{frame:06d}.png" and spec["frame_start"] <= frame <= spec["frame_end"]
                                 and (frame - spec["frame_start"]) % spec["frame_step"] == 0)
                    if known:
                        yield path / name
        finally:
            os.close(fd)

    def plan(self, owner, library_root, policy):
        root = str(Path(library_root).expanduser().resolve())
        now, candidates, protected, total, scanned = time.time(), [], {}, 0, 0
        truncated = False
        with self.journal.lock:
            rows = self.journal.db.execute("SELECT t.*,o.state,o.finished_at FROM temporary_outputs t "
                "JOIN operations o ON o.id=t.operation_id WHERE t.owner=? AND t.library_root=? ORDER BY o.finished_at",
                (owner, root)).fetchall()
        for row in rows:
            if row["pinned"] or row["state"] not in TERMINAL or row["finished_at"] is None:
                reason = "pinned_groups" if row["pinned"] else "active_or_unknown_groups"
                protected[reason] = protected.get(reason, 0) + 1
                continue
            spec = json.loads(row["spec"])
            # Validate ancestors before opening a sequence directory itself.
            try:
                with parent_fd(root, spec["path"]) as (fd, name):
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        raise ValueError("Symlink output")
            except (OSError, ValueError):
                protected["missing_or_unsafe_groups"] = protected.get("missing_or_unsafe_groups", 0) + 1
                continue
            for path in self.names(spec):
                scanned += 1
                if scanned > policy["max_scan_files"]:
                    truncated = True
                    break
                try:
                    with parent_fd(root, path) as (fd, name):
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("Nonregular output")
                except (OSError, ValueError):
                    protected["missing_or_unsafe_files"] = protected.get("missing_or_unsafe_files", 0) + 1
                    continue
                total += info.st_size
                if info.st_mtime > row["finished_at"]:
                    protected["modified_files"] = protected.get("modified_files", 0) + 1
                    continue
                age = now - max(info.st_mtime, row["finished_at"])
                if age < policy["min_age_seconds"]:
                    protected["recent_files"] = protected.get("recent_files", 0) + 1
                    continue
                candidates.append({"output_id": row["id"], "operation_id": row["operation_id"],
                    "path": str(path), "fingerprint": fingerprint(info), "age_seconds": age})
            if truncated:
                break
        candidates.sort(key=lambda item: -item["age_seconds"])
        selected, remaining = [], total
        for item in candidates:
            if item["age_seconds"] >= policy["max_age_seconds"] or remaining > policy["max_temporary_bytes"]:
                selected.append(item)
                remaining -= item["fingerprint"]["size"]
                if len(selected) >= policy["batch_files"]:
                    break
        with self.journal.lock:
            history = [r[0] for r in self.journal.db.execute("SELECT id FROM operations WHERE owner=? AND library_root=? AND "
                "state IN ('succeeded','failed','cancelled') AND finished_at<? AND details_pruned=0 "
                "AND NOT EXISTS(SELECT 1 FROM temporary_outputs t WHERE t.operation_id=operations.id AND t.pinned=1) "
                "AND NOT EXISTS(SELECT 1 FROM metadata m WHERE m.name='retention-pin:'||operations.id AND m.value='true') "
                "ORDER BY finished_at LIMIT 1000", (owner, root, now - policy["history_max_age_seconds"]))]
            identifier = uuid.uuid4().hex
            result = {"plan_id": identifier, "expires_at": now + 600, "files": selected, "history_operations": history,
                      "candidate_logical_bytes": sum(f["fingerprint"]["size"] for f in selected),
                      "scanned_temporary_bytes": total, "scan_truncated": truncated, "protected": protected,
                      "storage": snapshot(root), "note": "Only registered generated outputs; explicit paths and unknown files are preserved. History keeps request-ID tombstones."}
            self.journal.db.execute("DELETE FROM retention_plans WHERE expires<?", (now,))
            self.journal.db.execute("INSERT INTO retention_plans VALUES (?,?,?,?,?)", (identifier, owner, root, now + 600, json.dumps(result)))
            self.journal.db.commit()
        return result

    def apply(self, owner, library_root, policy, identifier):
        root = str(Path(library_root).expanduser().resolve())
        with self.journal.lock:
            row = self.journal.db.execute("SELECT * FROM retention_plans WHERE id=? AND owner=? AND library_root=?", (identifier, owner, root)).fetchone()
            if row is None or row["expires"] < time.time():
                raise ValueError("Cleanup plan missing or expired; preview again")
            original = json.loads(row["plan"])
        # Re-evaluate pins, operation state, ages and current policy at application.
        fresh = self.plan(owner, root, policy)
        permitted = {(f["output_id"], f["path"]): f for f in fresh["files"]}
        deleted, skipped, touched = [], [], {}
        for item in original["files"]:
            key = item["output_id"], item["path"]
            if key not in permitted or permitted[key]["fingerprint"] != item["fingerprint"]:
                skipped.append(item["path"])
                continue
            with self.journal.lock:
                record = self.journal.db.execute("SELECT t.*,o.state FROM temporary_outputs t JOIN operations o ON o.id=t.operation_id WHERE t.id=?", (item["output_id"],)).fetchone()
                if record is None or record["pinned"] or record["state"] not in TERMINAL:
                    skipped.append(item["path"])
                    continue
                try:
                    with parent_fd(root, item["path"]) as (fd, name):
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or fingerprint(info) != item["fingerprint"]:
                            skipped.append(item["path"])
                            continue
                        os.unlink(name, dir_fd=fd)
                    deleted.append(item)
                    touched[record["id"]] = json.loads(record["spec"])
                except OSError:
                    skipped.append(item["path"])
        with self.journal.lock:
            db = self.journal.db
            for output_id, spec in touched.items():
                if spec["kind"] == "sequence":
                    try:
                        with parent_fd(root, spec["path"]) as (fd, name):
                            os.rmdir(name, dir_fd=fd)  # Empty directories only; never recursive deletion.
                    except OSError:
                        pass
                if not Path(spec["path"]).exists():
                    db.execute("DELETE FROM temporary_outputs WHERE id=?", (output_id,))
            pruned = 0
            for operation_id in set(original["history_operations"]) & set(fresh["history_operations"]):
                pinned = db.execute("SELECT 1 FROM temporary_outputs WHERE operation_id=? AND pinned=1", (operation_id,)).fetchone()
                manual_pin = db.execute("SELECT value FROM metadata WHERE name=?", ("retention-pin:" + operation_id,)).fetchone()
                if pinned or (manual_pin and manual_pin[0] == "true"):
                    continue
                pruned += db.execute("UPDATE operations SET params='{}',result=NULL,progress=NULL,details_pruned=1 "
                    "WHERE id=? AND owner=? AND state IN ('succeeded','failed','cancelled')", (operation_id, owner)).rowcount
            db.execute("DELETE FROM retention_plans WHERE id IN (?,?)", (identifier, fresh["plan_id"]))
            db.commit()
        return {"deleted_files": [i["path"] for i in deleted], "deleted_logical_bytes": sum(i["fingerprint"]["size"] for i in deleted),
                "skipped_files": skipped, "history_details_pruned": pruned, "storage": snapshot(root),
                "note": "Logical file bytes are not a guarantee of physical space reclaimed (for example with filesystem snapshots)."}

    def automatic(self, owner, library_root, policy):
        key = owner, library_root
        with self.journal.lock:
            name = "retention-last:" + hashlib.sha256((owner + library_root).encode()).hexdigest()
            previous = self.journal.db.execute("SELECT value FROM metadata WHERE name=?", (name,)).fetchone()
            last = max(self.last_run.get(key, 0), json.loads(previous[0]).get("completed_at", 0) if previous else 0)
            if not policy["enabled"] or key in self.busy or len(self.busy) >= 2 or time.time() - last < policy["interval_seconds"]:
                return {"scheduled": False}
            self.busy.add(key)
            self.last_run[key] = time.time()
        def work():
            try:
                plan = self.plan(owner, library_root, policy)
                result = self.apply(owner, library_root, policy, plan["plan_id"])
                result["completed_at"] = time.time()
            except Exception as exc:
                result = {"error": str(exc), "completed_at": time.time()}
            finally:
                with self.journal.lock:
                    self.busy.discard(key)
                    name = "retention-last:" + hashlib.sha256((owner + library_root).encode()).hexdigest()
                    self.journal.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (name, json.dumps(result)))
                    self.journal.db.commit()
        thread = threading.Thread(target=work, daemon=True)
        with self.journal.lock:
            self.threads = [t for t in self.threads if t.is_alive()]
            self.threads.append(thread)
            thread.start()
        return {"scheduled": True}

    def close(self):
        for thread in self.threads:
            thread.join(timeout=15)

    def status(self, owner, library_root, policy, offset=0, limit=100):
        root = str(Path(library_root).expanduser().resolve())
        key = "retention-last:" + hashlib.sha256((owner + root).encode()).hexdigest()
        with self.journal.lock:
            rows = self.journal.db.execute("SELECT pinned,count(*) FROM temporary_outputs WHERE owner=? AND library_root=? GROUP BY pinned", (owner, root)).fetchall()
            last = self.journal.db.execute("SELECT value FROM metadata WHERE name=?", (key,)).fetchone()
            groups = self.journal.db.execute("SELECT t.*,o.state FROM temporary_outputs t JOIN operations o ON o.id=t.operation_id "
                "WHERE t.owner=? AND t.library_root=? ORDER BY t.id LIMIT ? OFFSET ?", (owner, root, min(200, max(1, limit)), max(0, offset))).fetchall()
            return {"policy": policy, "registered_output_groups": sum(r[1] for r in rows),
                    "pinned_output_groups": sum(r[1] for r in rows if r[0]),
                    "output_groups": [{"operation_id": r["operation_id"], "path": json.loads(r["spec"])["path"],
                                       "pinned": bool(r["pinned"]), "state": r["state"]} for r in groups],
                    "next_offset": max(0, offset) + len(groups) if max(0, offset) + len(groups) < sum(r[1] for r in rows) else None,
                    "maintenance_running": (owner, root) in self.busy,
                    "last_maintenance": json.loads(last[0]) if last else None, "storage": snapshot(root)}

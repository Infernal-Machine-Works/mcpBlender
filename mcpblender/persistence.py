"""Small persistent JSON registry shared by reconnecting MCP frontends."""
import json
import sqlite3
import threading
import time
from collections.abc import MutableMapping
from pathlib import Path

from .wire import private_directory


class Records(MutableMapping):
    def __init__(self, directory):
        self.path = Path(directory) / "artifacts.sqlite3"
        self.lock = threading.RLock()
        self.db = None

    def connect(self):
        if self.db is None:
            private_directory(self.path.parent)
            self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            self.path.chmod(0o600)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self.db.commit()
        return self.db

    def __getitem__(self, key):
        with self.lock:
            row = self.connect().execute("SELECT value FROM artifacts WHERE id=?", (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            return json.loads(row[0])

    def __setitem__(self, key, value):
        with self.lock:
            db = self.connect()
            db.execute("INSERT OR REPLACE INTO artifacts VALUES (?,?)", (key, json.dumps(value)))
            db.commit()

    def __delitem__(self, key):
        with self.lock:
            db = self.connect()
            if not db.execute("DELETE FROM artifacts WHERE id=?", (key,)).rowcount:
                raise KeyError(key)
            db.commit()

    def __iter__(self):
        with self.lock:
            return iter([row[0] for row in self.connect().execute("SELECT id FROM artifacts")])

    def __len__(self):
        with self.lock:
            return self.connect().execute("SELECT count(*) FROM artifacts").fetchone()[0]

    def prune_missing(self, owner):
        """Drop expired metadata for missing files; never delete the actual file."""
        with self.lock:
            db, removed = self.connect(), 0
            for identifier, value in db.execute("SELECT id,value FROM artifacts").fetchall():
                record = json.loads(value)
                if record["owner"] == owner and record.get("expires", 0) < time.time() and not Path(record["path"]).is_file():
                    removed += db.execute("DELETE FROM artifacts WHERE id=?", (identifier,)).rowcount
            db.commit()
            return removed

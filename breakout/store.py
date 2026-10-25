import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


def uid():
    return str(uuid.uuid4())


def home_path():
    return Path(os.environ.get("BREAKOUT_HOME", Path.home() / "Library/Application Support/Breakout"))


class Store:
    def __init__(self, home=None):
        self.home = Path(home or home_path()).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.home.stat().st_uid != os.getuid():
            raise ValueError("State directory must belong to the current user")
        self.home.chmod(0o700)
        self.db = sqlite3.connect(self.home / "state.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS breakouts(id TEXT PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, participant INTEGER NOT NULL,
          status TEXT NOT NULL, context TEXT NOT NULL, response TEXT, error TEXT, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(provider TEXT NOT NULL, session_id TEXT NOT NULL,
          data TEXT NOT NULL, PRIMARY KEY(provider, session_id));
        CREATE TABLE IF NOT EXISTS deliveries(run_id TEXT NOT NULL, revision INTEGER NOT NULL,
          provider TEXT NOT NULL, session_id TEXT NOT NULL, status TEXT NOT NULL,
          PRIMARY KEY(run_id, revision, provider, session_id));
        CREATE TABLE IF NOT EXISTS consents(id TEXT NOT NULL, project TEXT NOT NULL, created REAL NOT NULL,
          PRIMARY KEY(id, project));
        ''')
        self.db.commit()
        (self.home / "state.sqlite3").chmod(0o600)

    def save(self, run):
        self.db.execute("INSERT INTO breakouts VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,updated=excluded.updated",
                        (run["id"], json.dumps(run), time.time()))

    def get(self, run_id):
        row = self.db.execute("SELECT state FROM breakouts WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown breakout: " + run_id)
        return json.loads(row[0])

    def all(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT state FROM breakouts ORDER BY updated")]

    def register(self, data):
        if data.get("provider") not in ("codex", "claude"):
            raise ValueError("Unknown provider")
        for key in ("session_id", "project", "title"):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(f"Session requires {key}")
        data = {k: data[k] for k in ("provider", "session_id", "project", "title", "activity", "child", "boundary", "model") if k in data}
        data["updated"] = time.time()
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES(?,?,?) ON CONFLICT(provider,session_id) DO UPDATE SET data=excluded.data",
                            (data["provider"], data["session_id"], json.dumps(data)))
        return data

    def sessions(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM sessions")]

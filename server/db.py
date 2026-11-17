"""SQLite 저장소(사용자·작업)."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member',          -- admin | member
  status TEXT NOT NULL DEFAULT 'pending',       -- pending | approved | blocked
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                           -- lesson | interview
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',        -- queued | running | done | error
  step TEXT NOT NULL DEFAULT '',
  progress INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}',
  usage TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (email TEXT, ip TEXT, at REAL);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c = c
    return c


def init() -> None:
    conn().executescript(SCHEMA)


def q(sql: str, *args) -> list[sqlite3.Row]:
    return conn().execute(sql, args).fetchall()


def one(sql: str, *args):
    return conn().execute(sql, args).fetchone()


def run(sql: str, *args) -> int:
    return conn().execute(sql, args).lastrowid


def update_job(job_id: str, **fields) -> None:
    for k in ("meta", "usage"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn().execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def job(job_id: str):
    r = one("SELECT * FROM jobs WHERE id=?", job_id)
    if not r:
        return None
    d = dict(r)
    d["meta"] = json.loads(d["meta"] or "{}")
    d["usage"] = json.loads(d["usage"] or "{}")
    return d

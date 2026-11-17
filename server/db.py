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
CREATE TABLE IF NOT EXISTS examinees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT NOT NULL,                          -- 유특 | 초특 | 중특
  seq INTEGER NOT NULL DEFAULT 0,               -- 연번
  round TEXT NOT NULL DEFAULT '',               -- 회수
  name TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  region TEXT NOT NULL DEFAULT '',
  score TEXT NOT NULL DEFAULT '',               -- 1차 컷플
  upload_date TEXT NOT NULL DEFAULT '',         -- 영상 업로드 날짜
  email TEXT NOT NULL DEFAULT '',
  request TEXT NOT NULL DEFAULT '',             -- 요구사항
  video_url TEXT NOT NULL DEFAULT '',           -- 영상링크
  note TEXT NOT NULL DEFAULT '',                -- 비고
  crew_id INTEGER,                              -- 담당자(users.id)
  crew_text TEXT NOT NULL DEFAULT '',           -- 시트에 적혀 있던 담당자 이름(계정과 연결되지 않은 경우)
  done_date TEXT NOT NULL DEFAULT '',           -- 피드백 완료일자
  personal TEXT NOT NULL DEFAULT '',            -- 개인일정
  settle TEXT NOT NULL DEFAULT '',              -- 정산
  problem_id TEXT NOT NULL DEFAULT '',          -- 문제 은행에서 고른 문제
  problem_title TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',        -- form | sheet | manual
  src_key TEXT NOT NULL DEFAULT '',             -- 같은 파일을 다시 올려도 중복으로 들어가지 않게 하는 키
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# 나중에 추가된 열(이미 만들어진 DB에는 ALTER TABLE 로 붙인다)
MIGRATIONS = {
    "users": {"level": "TEXT NOT NULL DEFAULT ''", "nickname": "TEXT NOT NULL DEFAULT ''",
              "ord": "INTEGER NOT NULL DEFAULT 0"},
    "jobs": {"examinee_id": "INTEGER"},
}


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
    c = conn()
    c.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def setting(key: str, default: str = "") -> str:
    r = one("SELECT value FROM settings WHERE key=?", key)
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    conn().execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))


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

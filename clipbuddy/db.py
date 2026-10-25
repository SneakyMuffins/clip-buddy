"""SQLite store. One connection shared by the server and the worker thread, guarded by a lock."""
import json
import sqlite3
import threading
import time

from .config import DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  game TEXT, kind TEXT,             -- kind: replay | recording
  start_ts REAL, duration REAL, size INTEGER, mtime REAL,
  width INTEGER, height INTEGER, fps REAL, vcodec TEXT, bitrate INTEGER,
  status TEXT DEFAULT 'new',        -- new | keep | temp | trash
  expires REAL, rating INTEGER, title TEXT, tags TEXT,
  keep_start REAL, keep_end REAL,   -- chosen range; NULL = whole clip
  sug_start REAL, sug_end REAL, sug_whole INTEGER, peak REAL, strength REAL,
  quiet INTEGER DEFAULT 0,
  analyzed INTEGER DEFAULT 0,       -- 0 pending, 1 done, -1 failed
  trimmed INTEGER DEFAULT 0, compressed INTEGER DEFAULT 0,
  orig_size INTEGER,                -- size before Clip Buddy trimmed/compressed it
  prev_path TEXT, prev_status TEXT, trashed_at REAL, note TEXT,
  reviewed_at REAL, busy INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  clip_id INTEGER, kind TEXT, args TEXT,
  state TEXT DEFAULT 'pending',     -- pending | running | done | error | cancelled
  not_before REAL, created REAL, finished REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value REAL);
CREATE TABLE IF NOT EXISTS suggestions (
  clip_id INTEGER PRIMARY KEY, sug_start REAL, sug_end REAL, sug_whole INTEGER,
  final_start REAL, final_end REAL, duration REAL
);
"""

_lock = threading.RLock()
_conn = sqlite3.connect(DATA / "clipbuddy.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(SCHEMA)


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, args).fetchall()]


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql, args=()):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


def update(clip_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    x(f"UPDATE clips SET {cols} WHERE id=?", (*fields.values(), clip_id))


def clip(clip_id):
    return one("SELECT * FROM clips WHERE id=?", (clip_id,))


def add_job(clip_id, kind, args=None, delay=0.0):
    now = time.time()
    return x("INSERT INTO jobs(clip_id, kind, args, not_before, created) VALUES (?,?,?,?,?)",
             (clip_id, kind, json.dumps(args or {}), now + delay, now))


def bump_stat(key, amount):
    x("INSERT INTO stats(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = value + ?",
      (key, amount, amount))


def stat(key):
    r = one("SELECT value FROM stats WHERE key=?", (key,))
    return r["value"] if r else 0

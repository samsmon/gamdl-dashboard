import json
import sqlite3
import threading
import time

from app import settings as st

_ITEM_COLS = {
    "url", "original_url", "kind", "ext_id", "track_id", "status", "position", "title", "artist",
    "expected_tracks", "url_i", "url_n", "track_i", "track_n", "current_title", "errors",
    "output_path", "size_bytes", "error_msg", "findings", "created_at", "started_at", "finished_at",
    "attempts", "not_before", "codec", "classification", "library_status",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, original_url TEXT, kind TEXT, ext_id TEXT,
  track_id TEXT, status TEXT NOT NULL DEFAULT 'queued', position REAL NOT NULL DEFAULT 0,
  title TEXT, artist TEXT, expected_tracks INTEGER, url_i INTEGER, url_n INTEGER,
  track_i INTEGER, track_n INTEGER, current_title TEXT, errors INTEGER NOT NULL DEFAULT 0,
  output_path TEXT, size_bytes INTEGER, error_msg TEXT, findings TEXT,
  created_at REAL, started_at REAL, finished_at REAL,
  attempts INTEGER NOT NULL DEFAULT 0, not_before REAL, codec TEXT, classification TEXT, library_status TEXT);
CREATE TABLE IF NOT EXISTS tracks(
  item_id INTEGER NOT NULL, idx INTEGER NOT NULL, title TEXT, status TEXT, reason TEXT,
  PRIMARY KEY(item_id, idx));
CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, ts REAL, level TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS finished_tracks(ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            import os
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # items
    def add_item(self, url, original_url, kind, ext_id, track_id=None, title=None, artist=None, expected_tracks=None):
        with self._lock:
            pos = self._db.execute("SELECT COALESCE(MAX(position),0)+1 FROM items").fetchone()[0]
        cur = self._exec(
            "INSERT INTO items(url,original_url,kind,ext_id,track_id,title,artist,expected_tracks,position,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (url, original_url, kind, ext_id, track_id, title, artist, expected_tracks, pos, time.time()))
        return cur.lastrowid

    def get_item(self, item_id):
        return self._one("SELECT * FROM items WHERE id=?", (item_id,))

    def list_items(self):
        return self._all("SELECT * FROM items ORDER BY position, id")

    def find_by_key(self, kind, ext_id, track_id):
        return self._one(
            "SELECT * FROM items WHERE kind=? AND ext_id=? AND COALESCE(track_id,'')=COALESCE(?,'')"
            " AND status!='cancelled' LIMIT 1", (kind, ext_id, track_id))

    def update_item(self, item_id, **fields):
        bad = set(fields) - _ITEM_COLS
        if bad:
            raise ValueError(f"unknown item columns: {sorted(bad)}")
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE items SET {sets} WHERE id=?", (*fields.values(), item_id))

    def next_queued(self, now=None):
        now = time.time() if now is None else now
        return self._one(
            "SELECT * FROM items WHERE status='queued' AND (not_before IS NULL OR not_before<=?)"
            " ORDER BY position, id LIMIT 1", (now,))

    def reorder(self, ids):
        with self._lock:
            for pos, i in enumerate(ids, start=1):
                self._db.execute("UPDATE items SET position=? WHERE id=?", (pos, i))
            self._db.commit()

    def remove_item(self, item_id):
        self._exec("DELETE FROM tracks WHERE item_id=?", (item_id,))
        self._exec("DELETE FROM items WHERE id=?", (item_id,))

    def recover_after_crash(self):
        cur = self._exec("UPDATE items SET status='queued' WHERE status IN ('downloading','waiting')")
        return cur.rowcount

    # tracks
    def clear_tracks(self, item_id):
        self._exec("DELETE FROM tracks WHERE item_id=?", (item_id,))

    def upsert_track(self, item_id, idx, title, status, reason=None):
        self._exec(
            "INSERT INTO tracks(item_id,idx,title,status,reason) VALUES(?,?,?,?,?)"
            " ON CONFLICT(item_id,idx) DO UPDATE SET title=excluded.title,status=excluded.status,reason=excluded.reason",
            (item_id, idx, title, status, reason))

    def list_tracks(self, item_id):
        return self._all("SELECT * FROM tracks WHERE item_id=? ORDER BY idx", (item_id,))

    # log
    def add_log(self, item_id, level, text, ts):
        cur = self._exec("INSERT INTO log(item_id,ts,level,text) VALUES(?,?,?,?)", (item_id, ts, level, text))
        self._exec("DELETE FROM log WHERE id<=?", (cur.lastrowid - 2000,))

    def tail_log(self, limit=200):
        rows = self._all("SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,))
        return list(reversed(rows))

    # cap
    def record_track(self, ts):
        self._exec("INSERT INTO finished_tracks(ts) VALUES(?)", (ts,))

    def count_tracks_since(self, ts):
        return self._one("SELECT COUNT(*) AS n FROM finished_tracks WHERE ts>=?", (ts,))["n"]

    def oldest_track_since(self, ts):
        r = self._one("SELECT MIN(ts) AS t FROM finished_tracks WHERE ts>=?", (ts,))
        return r["t"]

    # settings / flags
    def get_flag(self, key):
        r = self._one("SELECT v FROM kv WHERE k=?", (key,))
        return r["v"] if r else None

    def set_flag(self, key, value):
        if value is None:
            self._exec("DELETE FROM kv WHERE k=?", (key,))
        else:
            self._exec("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))

    def get_settings(self):
        raw = self.get_flag("settings")
        return {**st.DEFAULTS, **(json.loads(raw) if raw else {})}

    def put_settings(self, patch):
        clean = st.validate(patch)
        merged = {**self.get_settings(), **clean}
        st.check_merged(merged)
        self.set_flag("settings", json.dumps(merged))
        return merged

    def get_banner(self):
        raw = self.get_flag("banner")
        return json.loads(raw) if raw else None

    def set_banner(self, kind, reason):
        self.set_flag("banner", json.dumps({"kind": kind, "reason": reason}))

    def clear_banner(self):
        self.set_flag("banner", None)

    # history
    def history_totals(self):
        r = self._one("SELECT COALESCE(SUM(size_bytes),0) AS b, COALESCE(SUM(track_n),0) AS t FROM items WHERE status='done'")
        return r["b"], r["t"]

    def done_items(self):
        return self._all("SELECT * FROM items WHERE status='done' ORDER BY finished_at DESC, id DESC")

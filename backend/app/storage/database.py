"""SQLite access. One short-lived connection per operation keeps it thread-safe under FastAPI.

Schema v2 is multi-user: every personal table carries a user_id. A v1 (single-user) database is upgraded in place:
the old tables are renamed legacy_* and the first person to sign in can claim them (see claim_legacy).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, google_sub TEXT UNIQUE NOT NULL, email TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '', picture TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, last_login TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY, provider TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS checklist_log (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, activity_id TEXT NOT NULL, status TEXT NOT NULL,
  actual_km REAL, actual_minutes INTEGER, notes TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual',
  strava_id INTEGER, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, activity_id));
CREATE TABLE IF NOT EXISTS readiness (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, date TEXT NOT NULL, sleep_hours REAL NOT NULL,
  rhr_delta INTEGER NOT NULL, soreness INTEGER NOT NULL, mood TEXT NOT NULL, result TEXT NOT NULL,
  flags TEXT NOT NULL DEFAULT '', PRIMARY KEY (user_id, date));
CREATE TABLE IF NOT EXISTS strava_tokens (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, athlete_id INTEGER, access_token TEXT NOT NULL,
  refresh_token TEXT NOT NULL, expires_at INTEGER NOT NULL, scope TEXT, last_sync TEXT);
CREATE TABLE IF NOT EXISTS strava_activities (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, id INTEGER NOT NULL, name TEXT NOT NULL,
  sport_type TEXT NOT NULL, start_local TEXT NOT NULL, distance_m REAL NOT NULL, moving_time_s INTEGER NOT NULL,
  elapsed_time_s INTEGER NOT NULL DEFAULT 0, avg_hr REAL, max_hr REAL, elevation_m REAL NOT NULL DEFAULT 0,
  mapped_type TEXT NOT NULL, matched_activity_id TEXT, PRIMARY KEY (user_id, id));
"""

# table -> column that only exists in the v2 layout
_V2_MARKER = {"checklist_log": "user_id", "readiness": "user_id", "strava_tokens": "user_id",
              "strava_activities": "user_id", "oauth_states": "provider"}


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connect() as c:
            for table, marker in _V2_MARKER.items():
                cols = [r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
                if cols and marker not in cols:          # v1 table: keep the data aside
                    c.execute(f"DROP TABLE IF EXISTS legacy_{table}")
                    c.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
            c.executescript(SCHEMA)

    def has_legacy(self) -> bool:
        with self.connect() as c:
            return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name IN ('legacy_checklist_log','legacy_strava_tokens')").fetchone())

    def claim_legacy(self, user_id: int) -> int:
        """Moves v1 single-user data to `user_id`. Returns the number of checklist rows moved."""
        moved = 0
        with self.connect() as c:
            names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "legacy_checklist_log" in names:
                c.execute("""INSERT OR IGNORE INTO checklist_log(user_id,activity_id,status,actual_km,actual_minutes,notes,source,strava_id,updated_at)
                             SELECT ?,activity_id,status,actual_km,actual_minutes,notes,source,strava_id,updated_at FROM legacy_checklist_log""", (user_id,))
                moved = c.execute("SELECT changes() AS n").fetchone()["n"]
            if "legacy_readiness" in names:
                c.execute("""INSERT OR IGNORE INTO readiness(user_id,date,sleep_hours,rhr_delta,soreness,mood,result,flags)
                             SELECT ?,date,sleep_hours,rhr_delta,soreness,mood,result,flags FROM legacy_readiness""", (user_id,))
            if "legacy_strava_tokens" in names:
                c.execute("""INSERT OR IGNORE INTO strava_tokens(user_id,athlete_id,access_token,refresh_token,expires_at,scope,last_sync)
                             SELECT ?,athlete_id,access_token,refresh_token,expires_at,scope,last_sync FROM legacy_strava_tokens""", (user_id,))
            if "legacy_strava_activities" in names:
                c.execute("""INSERT OR IGNORE INTO strava_activities(user_id,id,name,sport_type,start_local,distance_m,moving_time_s,elapsed_time_s,avg_hr,max_hr,elevation_m,mapped_type,matched_activity_id)
                             SELECT ?,id,name,sport_type,start_local,distance_m,moving_time_s,elapsed_time_s,avg_hr,max_hr,elevation_m,mapped_type,matched_activity_id FROM legacy_strava_activities""", (user_id,))
            for t in ("checklist_log", "readiness", "strava_tokens", "strava_activities", "oauth_states"):
                c.execute(f"DROP TABLE IF EXISTS legacy_{t}")
        return moved

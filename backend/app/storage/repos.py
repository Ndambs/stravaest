"""Repositories: the only place that knows SQL. Personal repos are bound to one user_id."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional

from ..domain.models import LogEntry, Readiness, StravaActivity, User
from .database import Database


# ---------------------------------------------------------------- accounts
class UserRepo:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _user(r) -> User:
        return User(r["id"], r["email"], r["name"], r["picture"])

    def upsert_google(self, sub: str, email: str, name: str, picture: str, now_iso: str) -> User:
        with self.db.connect() as c:
            c.execute("""INSERT INTO users(google_sub,email,name,picture,created_at,last_login) VALUES(?,?,?,?,?,?)
                         ON CONFLICT(google_sub) DO UPDATE SET email=excluded.email, name=excluded.name,
                         picture=excluded.picture, last_login=excluded.last_login""",
                      (sub, email, name, picture, now_iso, now_iso))
            r = c.execute("SELECT * FROM users WHERE google_sub=?", (sub,)).fetchone()
        return self._user(r)

    def get(self, user_id: int) -> Optional[User]:
        with self.db.connect() as c:
            r = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._user(r) if r else None

    def count(self) -> int:
        with self.db.connect() as c:
            return c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


class SessionRepo:
    def __init__(self, db: Database):
        self.db = db

    def create(self, token_hash: str, user_id: int, now: int, expires_at: int) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            c.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                      (token_hash, user_id, now, expires_at))

    def user_for(self, token_hash: str, now: int) -> Optional[User]:
        with self.db.connect() as c:
            r = c.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                             WHERE s.token_hash=? AND s.expires_at > ?""", (token_hash, now)).fetchone()
        return UserRepo._user(r) if r else None

    def delete(self, token_hash: str) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


class OAuthStateRepo:
    """Short-lived, single-use state (CSRF protection) for the Google and Strava OAuth round trips."""
    TTL = 900

    def __init__(self, db: Database):
        self.db = db

    def add(self, provider: str, state: str, payload: dict, now: int) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM oauth_states WHERE created_at < ?", (now - self.TTL,))
            c.execute("INSERT INTO oauth_states(state,provider,payload,created_at) VALUES(?,?,?,?)",
                      (state, provider, json.dumps(payload), now))

    def consume(self, provider: str, state: str, now: int) -> Optional[dict]:
        with self.db.connect() as c:
            r = c.execute("SELECT payload, created_at FROM oauth_states WHERE state=? AND provider=?",
                          (state, provider)).fetchone()
            c.execute("DELETE FROM oauth_states WHERE state=?", (state,))
        if not r or now - r["created_at"] > self.TTL:
            return None
        return json.loads(r["payload"])


def user_for_athlete(db: Database, athlete_id: int) -> Optional[int]:
    """Which user connected this Strava athlete? (used by the webhook)"""
    with db.connect() as c:
        r = c.execute("SELECT user_id FROM strava_tokens WHERE athlete_id=?", (athlete_id,)).fetchone()
    return r["user_id"] if r else None


# ---------------------------------------------------------------- personal data
class LogRepo:
    def __init__(self, db: Database, user_id: int):
        self.db, self.uid = db, user_id

    def all(self) -> dict[str, LogEntry]:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM checklist_log WHERE user_id=?", (self.uid,)).fetchall()
        return {r["activity_id"]: LogEntry(r["activity_id"], r["status"], r["actual_km"], r["actual_minutes"],
                                           r["notes"], r["source"], r["strava_id"], r["updated_at"]) for r in rows}

    def get(self, activity_id: str) -> Optional[LogEntry]:
        return self.all().get(activity_id)

    def upsert(self, e: LogEntry) -> None:
        with self.db.connect() as c:
            c.execute("""INSERT INTO checklist_log(user_id,activity_id,status,actual_km,actual_minutes,notes,source,strava_id,updated_at)
                         VALUES(?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(user_id,activity_id) DO UPDATE SET status=excluded.status, actual_km=excluded.actual_km,
                         actual_minutes=excluded.actual_minutes, notes=excluded.notes, source=excluded.source,
                         strava_id=excluded.strava_id, updated_at=excluded.updated_at""",
                      (self.uid, e.activity_id, e.status, e.actual_km, e.actual_minutes, e.notes, e.source, e.strava_id, e.updated_at))

    def delete(self, activity_id: str) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM checklist_log WHERE user_id=? AND activity_id=?", (self.uid, activity_id))


class ReadinessRepo:
    def __init__(self, db: Database, user_id: int):
        self.db, self.uid = db, user_id

    @staticmethod
    def _row(r) -> Readiness:
        return Readiness(date.fromisoformat(r["date"]), r["sleep_hours"], r["rhr_delta"], r["soreness"], r["mood"],
                         r["result"], [f for f in r["flags"].split("|") if f])

    def all(self) -> dict[date, Readiness]:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM readiness WHERE user_id=?", (self.uid,)).fetchall()
        return {date.fromisoformat(r["date"]): self._row(r) for r in rows}

    def get(self, d: date) -> Optional[Readiness]:
        return self.all().get(d)

    def upsert(self, r: Readiness) -> None:
        with self.db.connect() as c:
            c.execute("""INSERT INTO readiness(user_id,date,sleep_hours,rhr_delta,soreness,mood,result,flags) VALUES(?,?,?,?,?,?,?,?)
                         ON CONFLICT(user_id,date) DO UPDATE SET sleep_hours=excluded.sleep_hours, rhr_delta=excluded.rhr_delta,
                         soreness=excluded.soreness, mood=excluded.mood, result=excluded.result, flags=excluded.flags""",
                      (self.uid, r.date.isoformat(), r.sleep_hours, r.rhr_delta, r.soreness, r.mood, r.result, "|".join(r.flags)))


class StravaRepo:
    def __init__(self, db: Database, user_id: int):
        self.db, self.uid = db, user_id

    def get_tokens(self) -> Optional[dict]:
        with self.db.connect() as c:
            r = c.execute("SELECT * FROM strava_tokens WHERE user_id=?", (self.uid,)).fetchone()
        return dict(r) if r else None

    def save_tokens(self, athlete_id: Optional[int], access: str, refresh: str, expires_at: int, scope: str = "") -> None:
        with self.db.connect() as c:
            c.execute("""INSERT INTO strava_tokens(user_id,athlete_id,access_token,refresh_token,expires_at,scope)
                         VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                         athlete_id=COALESCE(excluded.athlete_id, strava_tokens.athlete_id),
                         access_token=excluded.access_token, refresh_token=excluded.refresh_token,
                         expires_at=excluded.expires_at, scope=COALESCE(NULLIF(excluded.scope,''), strava_tokens.scope)""",
                      (self.uid, athlete_id, access, refresh, expires_at, scope))

    def set_last_sync(self, iso: str) -> None:
        with self.db.connect() as c:
            c.execute("UPDATE strava_tokens SET last_sync=? WHERE user_id=?", (iso, self.uid))

    def clear_tokens(self) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM strava_tokens WHERE user_id=?", (self.uid,))

    @staticmethod
    def _act(r) -> StravaActivity:
        return StravaActivity(r["id"], r["name"], r["sport_type"], datetime.fromisoformat(r["start_local"]),
                              r["distance_m"], r["moving_time_s"], r["elapsed_time_s"], r["avg_hr"], r["max_hr"],
                              r["elevation_m"], r["mapped_type"], r["matched_activity_id"])

    def upsert_activity(self, a: StravaActivity) -> None:
        with self.db.connect() as c:
            c.execute("""INSERT INTO strava_activities(user_id,id,name,sport_type,start_local,distance_m,moving_time_s,elapsed_time_s,
                         avg_hr,max_hr,elevation_m,mapped_type,matched_activity_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(user_id,id) DO UPDATE SET name=excluded.name, sport_type=excluded.sport_type,
                         start_local=excluded.start_local, distance_m=excluded.distance_m, moving_time_s=excluded.moving_time_s,
                         elapsed_time_s=excluded.elapsed_time_s, avg_hr=excluded.avg_hr, max_hr=excluded.max_hr,
                         elevation_m=excluded.elevation_m, mapped_type=excluded.mapped_type""",
                      (self.uid, a.id, a.name, a.sport_type, a.start_local.isoformat(), a.distance_m, a.moving_time_s,
                       a.elapsed_time_s, a.avg_hr, a.max_hr, a.elevation_m, a.mapped_type, a.matched_activity_id))

    def activities(self) -> list[StravaActivity]:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM strava_activities WHERE user_id=? ORDER BY start_local", (self.uid,)).fetchall()
        return [self._act(r) for r in rows]

    def delete_activity(self, activity_id: int) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM strava_activities WHERE user_id=? AND id=?", (self.uid, activity_id))

    def set_matches(self, matches: dict[int, Optional[str]]) -> None:
        with self.db.connect() as c:
            c.execute("UPDATE strava_activities SET matched_activity_id=NULL WHERE user_id=?", (self.uid,))
            for sid, aid in matches.items():
                c.execute("UPDATE strava_activities SET matched_activity_id=? WHERE user_id=? AND id=?", (aid, self.uid, sid))

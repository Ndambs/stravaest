"""Pulls activities from Strava, matches them to the plan and auto-ticks the checklist."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Optional

from ...clock import Clock
from ...domain.models import DISTANCE_TYPES, DONE, StravaActivity
from ...plan.loader import Plan, normalise_title
from ...services.checklist_service import ChecklistService
from ...storage.repos import LogRepo, OAuthStateRepo, StravaRepo
from .client import StravaClient, StravaError, to_activity
from .mapper import map_sport_type
from .matcher import match_all


class StravaSyncService:
    def __init__(self, client: StravaClient, repo: StravaRepo, plan: Plan, checklist: ChecklistService,
                 logs: LogRepo, clock: Clock, states: OAuthStateRepo, user_id: int):
        self.client, self.repo, self.plan, self.checklist, self.logs, self.clock = client, repo, plan, checklist, logs, clock
        self.states, self.user_id = states, user_id

    # ---- OAuth ----
    def connect_url(self) -> str:
        state = secrets.token_urlsafe(24)
        url = self.client.authorize_url(state)
        self.states.add("strava", state, {"user_id": self.user_id}, int(self.clock.now().timestamp()))
        return url

    def handle_callback(self, code: str, state: str, scope: str = "") -> None:
        payload = self.states.consume("strava", state, int(self.clock.now().timestamp()))
        if not payload or payload.get("user_id") != self.user_id:     # state must belong to this signed-in user
            raise StravaError(400, "invalid or expired OAuth state")
        t = self.client.exchange_code(code)
        self.repo.save_tokens(t.athlete_id, t.access_token, t.refresh_token, t.expires_at, scope)

    def disconnect(self) -> None:
        self.repo.clear_tokens()

    def status(self) -> dict:
        t = self.repo.get_tokens()
        return {"configured": self.client.configured, "connected": bool(t),
                "athlete_id": t["athlete_id"] if t else None, "scope": t["scope"] if t else None,
                "last_sync": t["last_sync"] if t else None}

    def _token(self) -> str:
        t = self.repo.get_tokens()
        if not t:
            raise StravaError(401, "not connected to Strava")
        if t["expires_at"] - 60 <= int(self.clock.now().timestamp()):
            n = self.client.refresh(t["refresh_token"])
            self.repo.save_tokens(n.athlete_id or t["athlete_id"], n.access_token, n.refresh_token, n.expires_at)
            return n.access_token
        return t["access_token"]

    # ---- Sync ----
    def sync(self, full: bool = False) -> dict:
        token = self._token()
        tz = self.clock.tz
        start = datetime.combine(self.plan.start, datetime.min.time(), tzinfo=tz)
        end = datetime.combine(self.plan.end + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        last = (self.repo.get_tokens() or {}).get("last_sync")
        after = start
        if last and not full:
            after = max(start, datetime.fromisoformat(last) - timedelta(days=3))  # overlap catches late edits
        fetched = 0
        for raw in self.client.list_activities(token, int(after.timestamp()), int(end.timestamp())):
            self.repo.upsert_activity(self._convert(raw))
            fetched += 1
        self.repo.set_last_sync(self.clock.now().isoformat(timespec="seconds"))
        return {"fetched": fetched, **self.rematch()}

    def _convert(self, raw: dict) -> StravaActivity:
        a = to_activity(raw)
        pid = self.plan.title_index.get(normalise_title(a.name))
        a.mapped_type = map_sport_type(a.sport_type, a.name, pid)
        return a

    def rematch(self) -> dict:
        acts = self.repo.activities()
        by_id = {a.id: a for a in acts}
        logs = self.logs.all()
        manual = {k for k, v in logs.items() if v.source == "manual"}
        matches = match_all(acts, self.plan, taken=manual)
        self.repo.set_matches({sid: m.activity_id for sid, m in matches.items()})
        matched_ids, auto = set(), 0
        for sid, m in matches.items():
            if not m.activity_id:
                continue
            matched_ids.add(m.activity_id)
            s = by_id[sid]
            km = s.km if s.mapped_type in DISTANCE_TYPES else None
            self.checklist.set_status(m.activity_id, DONE, km, s.minutes, f"Strava: {s.name}", "strava", sid)
            auto += 1
        for aid, entry in logs.items():       # a Strava activity was deleted or re-matched
            if entry.source == "strava" and aid not in matched_ids:
                self.checklist.reset(aid)
        return {"matched": auto, "unplanned": sum(1 for m in matches.values() if not m.activity_id)}

    # ---- Webhook ----
    def handle_event(self, event: dict) -> dict:
        if event.get("object_type") != "activity":
            return {"ignored": True}
        t = self.repo.get_tokens()
        if not t or (t["athlete_id"] and event.get("owner_id") != t["athlete_id"]):
            return {"ignored": True}
        oid, aspect = int(event["object_id"]), event.get("aspect_type")
        if aspect == "delete":
            self.repo.delete_activity(oid)
        else:
            self.repo.upsert_activity(self._convert(self.client.get_activity(self._token(), oid)))
        return self.rematch()

    def activities_view(self) -> list[dict]:
        out = []
        for s in self.repo.activities():
            out.append({"id": s.id, "name": s.name, "sport_type": s.sport_type, "type": s.mapped_type,
                        "date": s.day.isoformat(), "km": s.km, "minutes": s.minutes, "avg_hr": s.avg_hr,
                        "matched_activity_id": s.matched_activity_id})
        return out

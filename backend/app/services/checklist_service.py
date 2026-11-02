"""Checklist: merges the static plan with what you have logged."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Optional

from ..clock import Clock
from ..domain.models import COUNTED, DONE, MISSED, MODIFIED, PENDING, SKIPPED, STORED_STATUSES, LogEntry, PlannedActivity
from ..plan.loader import Plan
from ..storage.repos import LogRepo, ReadinessRepo


class ChecklistError(ValueError):
    pass


def effective_status(act: PlannedActivity, log: Optional[LogEntry], today: date) -> str:
    if log:
        return log.status
    return MISSED if act.date < today else PENDING


class ChecklistService:
    def __init__(self, plan: Plan, logs: LogRepo, readiness: ReadinessRepo, clock: Clock):
        self.plan, self.logs, self.readiness, self.clock = plan, logs, readiness, clock

    def set_status(self, activity_id: str, status: str, actual_km: Optional[float] = None,
                   actual_minutes: Optional[int] = None, notes: str = "", source: str = "manual",
                   strava_id: Optional[int] = None) -> LogEntry:
        act = self.plan.activities.get(activity_id)
        if not act:
            raise ChecklistError(f"Unknown activity: {activity_id}")
        if status not in STORED_STATUSES:
            raise ChecklistError(f"Invalid status: {status}")
        if status == DONE and actual_km is not None and act.planned_km > 0 and abs(actual_km - act.planned_km) / act.planned_km > 0.2:
            status = MODIFIED  # ran or rode a clearly different distance than planned
        entry = LogEntry(activity_id, status, actual_km, actual_minutes, notes[:500], source, strava_id,
                         datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.logs.upsert(entry)
        return entry

    def reset(self, activity_id: str) -> None:
        if activity_id not in self.plan.activities:
            raise ChecklistError(f"Unknown activity: {activity_id}")
        self.logs.delete(activity_id)

    def _activity_view(self, a: PlannedActivity, logs: dict[str, LogEntry], today: date) -> dict:
        log = logs.get(a.id)
        v = asdict(a)
        v["date"] = a.date.isoformat()
        v["status"] = effective_status(a, log, today)
        v["log"] = asdict(log) if log else None
        return v

    def day_view(self, d: date) -> Optional[dict]:
        return self.range_view(d, d)[0] if self.plan.day(d) else None

    def range_view(self, a: date, b: date) -> list[dict]:
        logs, today, rd = self.logs.all(), self.clock.today(), self.readiness.all()
        out = []
        for day in self.plan.days_between(a, b):
            acts = [self._activity_view(x, logs, today) for x in day.activities]
            counted = [x for x in acts if not x["optional"]]
            done = [x for x in counted if x["status"] in COUNTED]
            r = rd.get(day.date)
            out.append({
                "date": day.date.isoformat(), "weekday": day.weekday, "week": day.week, "special": day.special,
                "activities": acts, "bonus_titles": day.bonus_titles,
                "completion": round(len(done) / len(counted), 2) if counted else None,
                "readiness": asdict(r) | {"date": r.date.isoformat()} if r else None,
            })
        return out

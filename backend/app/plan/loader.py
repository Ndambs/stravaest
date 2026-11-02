"""Loads the training plan (seed JSON) into indexed, immutable-ish domain objects."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

from ..domain.models import DayPlan, Goal, PlannedActivity, WeekPlan


def _d(s: str) -> date:
    return date.fromisoformat(s)


class Plan:
    def __init__(self, raw: dict):
        self.meta: dict = raw["meta"]
        self.library: list[dict] = raw["library"]
        self.start = _d(self.meta["start"])
        self.end = _d(self.meta["end"])
        self.goals = [Goal(g["id"], g["month"], g["type"], float(g["target"])) for g in raw["goals"]]
        self.weeks = [WeekPlan(w["n"], _d(w["start"]), _d(w["end"]), w["title"], w["gym_note"], w["deload"],
                               w["planned_ride_km"], w["planned_run_km"], w["planned_gym"], w.get("planned_walk_km", 0.0)) for w in raw["weeks"]]
        self.days: dict[date, DayPlan] = {}
        self.activities: dict[str, PlannedActivity] = {}
        self.title_index: dict[str, str] = {}   # normalised strava title -> activity id
        for d in raw["days"]:
            day = _d(d["date"])
            acts = [PlannedActivity(
                id=a["id"], date=day, type=a["type"], name=a["name"], week=a["week"], code=a.get("code"),
                planned_km=float(a["planned_km"]), planned_minutes=int(a["planned_minutes"]),
                intensity=a["intensity"], key=a["key"], hard=a["hard"], optional=a["optional"],
                strava_log=a["strava_log"], strava_title=a.get("strava_title"), details=a["details"])
                for a in d["activities"]]
            self.days[day] = DayPlan(day, d["weekday"], d["week"], d.get("special"), acts, d.get("bonus_titles"))
            for a in acts:
                self.activities[a.id] = a
                if a.strava_title:
                    self.title_index[normalise_title(a.strava_title)] = a.id
            for typ, title in (d.get("bonus_titles") or {}).items():
                self.title_index.setdefault(normalise_title(title), f"bonus:{day.isoformat()}:{typ}")

    # ---- lookups ----
    def day(self, d: date) -> Optional[DayPlan]:
        return self.days.get(d)

    def days_between(self, a: date, b: date) -> list[DayPlan]:
        out, cur = [], a
        while cur <= b:
            if cur in self.days:
                out.append(self.days[cur])
            cur += timedelta(days=1)
        return out

    def activities_between(self, a: date, b: date) -> list[PlannedActivity]:
        return [x for d in self.days_between(a, b) for x in d.activities]

    def week_of(self, d: date) -> Optional[WeekPlan]:
        for w in self.weeks:
            if w.start <= d <= w.start + timedelta(days=6):
                return w
        return None

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


def normalise_title(t: str) -> str:
    return " ".join(t.casefold().split())


def load_plan(path: str | Path) -> Plan:
    return Plan(json.loads(Path(path).read_text(encoding="utf-8")))

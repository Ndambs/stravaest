"""Pairs Strava activities with planned activities. Pure functions, easy to test."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from ...domain.models import DISTANCE_TYPES, RECOVERY, PlannedActivity, StravaActivity
from ...plan.loader import Plan, normalise_title


@dataclass
class Match:
    strava_id: int
    activity_id: Optional[str]    # None = unplanned extra
    reason: str                   # title | same_day | shifted | extra
    shifted_days: int = 0


def _compatible(a: PlannedActivity, s: StravaActivity) -> bool:
    if not a.strava_log:
        return False
    if a.type == s.mapped_type:
        return True
    return a.type == RECOVERY      # any light activity can be your Sunday session


def _score(a: PlannedActivity, s: StravaActivity, same_day: bool) -> float:
    score = 10.0 + (5.0 if same_day else 0.0)
    if a.type in DISTANCE_TYPES and a.planned_km > 0 and s.mapped_type == a.type:
        score += 10 - min(10.0, abs(s.km - a.planned_km) / a.planned_km * 10)
    return score


def match_all(strava: list[StravaActivity], plan: Plan, taken: set[str] | None = None) -> dict[int, Match]:
    """`taken` = planned activity ids that are already logged by hand (never overwritten)."""
    taken = set(taken or ())
    out: dict[int, Match] = {}
    acts = sorted(strava, key=lambda s: s.start_local)

    # Pass 0: you typed a title from the title bank -> strongest signal (within 3 days of the plan)
    for s in acts:
        pid = plan.title_index.get(normalise_title(s.name))
        if not pid or pid.startswith("bonus:"):
            continue
        a = plan.activities[pid]
        gap = abs((s.day - a.date).days)
        if pid not in taken and gap <= 3 and a.strava_log:
            out[s.id] = Match(s.id, pid, "title", (s.day - a.date).days)
            taken.add(pid)

    # Pass 1: same day, best compatible candidate
    for s in acts:
        if s.id in out:
            continue
        day = plan.day(s.day)
        cands = [a for a in (day.activities if day else []) if a.id not in taken and _compatible(a, s)]
        # a Sunday "recovery" slot is a fallback: prefer a type-specific planned activity first
        cands.sort(key=lambda a: (a.type == RECOVERY, -_score(a, s, True)))
        if cands:
            out[s.id] = Match(s.id, cands[0].id, "same_day")
            taken.add(cands[0].id)

    # Pass 2: done a day early or late
    for s in acts:
        if s.id in out:
            continue
        best: Optional[tuple[float, PlannedActivity, int]] = None
        for delta in (-1, 1):
            day = plan.day(s.day + timedelta(days=delta))
            for a in (day.activities if day else []):
                if a.id in taken or a.type != s.mapped_type or not a.strava_log:
                    continue
                sc = _score(a, s, False)
                if best is None or sc > best[0]:
                    best = (sc, a, delta)
        if best and best[0] >= 18:   # only when the distance is within ~20% of what was planned
            out[s.id] = Match(s.id, best[1].id, "shifted", -best[2])
            taken.add(best[1].id)
        else:
            out[s.id] = Match(s.id, None, "extra")
    return out

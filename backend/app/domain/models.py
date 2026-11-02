"""Plain domain objects. No framework imports: everything here is unit-testable."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

# Activity types
RUN, RIDE, GYM, ROPE, CORE, MOBILITY, RECOVERY, WALK = "run", "ride", "gym", "rope", "core", "mobility", "recovery", "walk"
ACTIVITY_TYPES = (RUN, RIDE, GYM, ROPE, CORE, MOBILITY, RECOVERY, WALK)
DISTANCE_TYPES = (RUN, RIDE, WALK)   # activities measured in km (monthly goals)

# Checklist statuses stored in the DB
DONE, MODIFIED, SKIPPED = "done", "modified", "skipped"
STORED_STATUSES = (DONE, MODIFIED, SKIPPED)
# Computed statuses shown in the UI
PENDING, MISSED = "pending", "missed"
COUNTED = (DONE, MODIFIED)


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str = ""
    picture: str = ""


@dataclass(frozen=True)
class PlannedActivity:
    id: str
    date: date
    type: str
    name: str
    week: int
    code: Optional[str] = None
    planned_km: float = 0.0
    planned_minutes: int = 0
    intensity: str = "easy"
    key: bool = False          # an important session for the week
    hard: bool = False         # counts towards the "hard days" budget (max 3 / week)
    optional: bool = False     # finishers: do not count towards completion
    strava_log: bool = True    # False = log inside another activity's description
    strava_title: Optional[str] = None
    details: str = ""


@dataclass
class DayPlan:
    date: date
    weekday: int
    week: int
    special: Optional[str]
    activities: list[PlannedActivity]
    bonus_titles: Optional[dict[str, str]] = None


@dataclass(frozen=True)
class WeekPlan:
    n: int
    start: date
    end: date
    title: str
    gym_note: str
    deload: bool
    planned_ride_km: float
    planned_run_km: float
    planned_gym: int
    planned_walk_km: float = 0.0


@dataclass(frozen=True)
class Goal:
    id: str
    month: str        # "2026-11"
    type: str         # ride | run
    target: float


@dataclass
class LogEntry:
    activity_id: str
    status: str
    actual_km: Optional[float] = None
    actual_minutes: Optional[int] = None
    notes: str = ""
    source: str = "manual"            # manual | strava
    strava_id: Optional[int] = None
    updated_at: str = ""


@dataclass
class Readiness:
    date: date
    sleep_hours: float
    rhr_delta: int
    soreness: int
    mood: str
    result: str = "green"
    flags: list[str] = field(default_factory=list)


@dataclass
class StravaActivity:
    id: int
    name: str
    sport_type: str
    start_local: datetime
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int = 0
    avg_hr: Optional[float] = None
    max_hr: Optional[float] = None
    elevation_m: float = 0.0
    mapped_type: str = GYM
    matched_activity_id: Optional[str] = None

    @property
    def day(self) -> date:
        return self.start_local.date()

    @property
    def km(self) -> float:
        return round(self.distance_m / 1000.0, 2)

    @property
    def minutes(self) -> int:
        return round(self.moving_time_s / 60)

    @property
    def pace_s_per_km(self) -> Optional[float]:
        if self.distance_m <= 0 or self.moving_time_s <= 0:
            return None
        return self.moving_time_s / (self.distance_m / 1000.0)

"""Strava sport types -> our activity types."""
from __future__ import annotations

from typing import Optional

from ...domain.models import CORE, GYM, MOBILITY, RECOVERY, RIDE, ROPE, RUN, WALK

RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
RIDE_TYPES = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide", "EBikeRide", "EMountainBikeRide"}
GYM_TYPES = {"WeightTraining", "Crossfit"}
MOBILITY_TYPES = {"Yoga", "Pilates"}
WALK_TYPES = {"Walk", "Hike"}
RECOVERY_TYPES = {"Swim", "Elliptical"}


def type_from_activity_id(activity_id: str) -> Optional[str]:
    """'2026-11-03-run' -> 'run'; 'bonus:2026-11-01:gym' -> 'gym'."""
    return activity_id.rsplit("-", 1)[-1] if activity_id[0].isdigit() else activity_id.rsplit(":", 1)[-1]


def map_sport_type(sport_type: str, name: str, matched_plan_id: Optional[str] = None) -> str:
    if sport_type in RUN_TYPES:
        return RUN
    if sport_type in RIDE_TYPES:
        return RIDE
    if sport_type in GYM_TYPES:
        return GYM
    if sport_type in MOBILITY_TYPES:
        return MOBILITY
    if sport_type in WALK_TYPES:
        return WALK
    if sport_type in RECOVERY_TYPES:
        return RECOVERY
    # "Workout" and anything unknown: the title bank or keywords decide
    if matched_plan_id:
        t = type_from_activity_id(matched_plan_id)
        if t in (GYM, ROPE, CORE, MOBILITY, RECOVERY):
            return t
    n = name.casefold()
    if any(k in n for k in ("rope", "skip", "jump")):
        return ROPE
    if any(k in n for k in ("core", "abs", "plank")):
        return CORE
    if any(k in n for k in ("mobility", "stretch", "yoga")):
        return MOBILITY
    return GYM

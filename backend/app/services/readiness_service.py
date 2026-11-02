"""Morning readiness check: sleep, resting HR, soreness, mood -> green / amber / red."""
from __future__ import annotations

from datetime import date

from ..domain.models import Readiness
from ..storage.repos import ReadinessRepo

GUIDANCE = {
    "green": "Train exactly as planned.",
    "amber": "Drop one interval rep or block (or go down one zone), gym at 2 sets, trim the spin by a third, skip rope and finishers.",
    "red": "Swap today for a 30 min easy walk or spin plus mobility. Do not cram missed km into the week: add at most 10 km to the next Thu or Sat ride.",
}


def grade(sleep_hours: float, rhr_delta: int, soreness: int, mood: str) -> tuple[str, list[str]]:
    reds, ambers, flags = 0, 0, []

    def mark(level: str, text: str) -> None:
        nonlocal reds, ambers
        if level == "red":
            reds += 1
        else:
            ambers += 1
        flags.append(f"{level}:{text}")

    if sleep_hours < 6:
        mark("red", "sleep under 6 h")
    elif sleep_hours < 7:
        mark("amber", "sleep 6-7 h")
    if rhr_delta >= 7:
        mark("red", "resting HR 7+ bpm above normal")
    elif rhr_delta >= 4:
        mark("amber", "resting HR 4-6 bpm above normal")
    if soreness >= 7:
        mark("red", "soreness 7+/10")
    elif soreness >= 4:
        mark("amber", "soreness 4-6/10")
    if mood == "low":
        mark("red", "dreading it")
    elif mood == "flat":
        mark("amber", "flat mood")
    result = "red" if reds or ambers >= 3 else "amber" if ambers else "green"
    return result, flags


class ReadinessService:
    def __init__(self, repo: ReadinessRepo):
        self.repo = repo

    def record(self, d: date, sleep_hours: float, rhr_delta: int, soreness: int, mood: str) -> dict:
        result, flags = grade(sleep_hours, rhr_delta, soreness, mood)
        r = Readiness(d, sleep_hours, rhr_delta, soreness, mood, result, flags)
        self.repo.upsert(r)
        return self.view(r)

    def get(self, d: date) -> dict | None:
        r = self.repo.get(d)
        return self.view(r) if r else None

    @staticmethod
    def view(r: Readiness) -> dict:
        return {"date": r.date.isoformat(), "sleep_hours": r.sleep_hours, "rhr_delta": r.rhr_delta,
                "soreness": r.soreness, "mood": r.mood, "result": r.result, "flags": r.flags,
                "guidance": GUIDANCE[r.result]}

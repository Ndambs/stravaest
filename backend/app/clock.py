"""Injectable clock so analysis can be tested for any 'today'."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


class Clock:
    def __init__(self, utc_offset_hours: int = 3):
        self.tz = timezone(timedelta(hours=utc_offset_hours))  # Nairobi, no DST

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()


class FixedClock(Clock):
    def __init__(self, today: date, utc_offset_hours: int = 3):
        super().__init__(utc_offset_hours)
        self._today = today

    def now(self) -> datetime:
        return datetime(self._today.year, self._today.month, self._today.day, 12, 0, tzinfo=self.tz)

    def today(self) -> date:
        return self._today

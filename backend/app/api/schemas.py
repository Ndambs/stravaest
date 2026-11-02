from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ChecklistUpdate(BaseModel):
    status: Literal["done", "modified", "skipped"]
    actual_km: Optional[float] = Field(default=None, ge=0, le=500)
    actual_minutes: Optional[int] = Field(default=None, ge=0, le=1000)
    notes: str = Field(default="", max_length=500)


class ReadinessInput(BaseModel):
    sleep_hours: float = Field(ge=0, le=14)
    rhr_delta: int = Field(ge=-15, le=40, description="bpm above your normal resting heart rate")
    soreness: int = Field(ge=1, le=10)
    mood: Literal["good", "flat", "low"]

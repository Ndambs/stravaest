import tempfile
from datetime import date
from pathlib import Path

from app.clock import FixedClock
from app.config import Settings
from app.container import Container


def make_settings(**overrides) -> Settings:
    tmp = tempfile.mkdtemp()
    base = dict(db_path=str(Path(tmp) / "t.db"), strava_client_id="cid", strava_client_secret="sec",
                google_client_id="gid", google_client_secret="gsec", allowed_emails=("me@example.com",))
    base.update(overrides)
    return Settings(**base)


def make_app(today: date, http=None, google_http=None, **overrides) -> Container:
    return Container(make_settings(**overrides), http=http, clock=FixedClock(today), google_http=google_http)


def make_container(today: date, http=None, **overrides):
    """Returns the per-user services for a signed-in test user (attributes: plan, checklist, analysis, strava...)."""
    c = make_app(today, http=http, **overrides)
    user = c.users.upsert_google("sub-1", "me@example.com", "Me", "", "2026-11-01T00:00:00")
    scope = c.scope(user.id)
    scope.container, scope.user = c, user
    return scope


def tick_all(c, start: date, end: date, factor: float = 1.0, only=None, source="manual"):
    """Mark every counted planned activity between start and end as done (optionally scaling km)."""
    for a in c.plan.activities_between(start, end):
        if a.optional or (only and a.type not in only):
            continue
        km = round(a.planned_km * factor, 1) if a.planned_km else None
        c.checklist.set_status(a.id, "done", km, a.planned_minutes or None, source=source)

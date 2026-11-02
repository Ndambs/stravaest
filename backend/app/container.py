"""Composition root. `Container` holds shared pieces; `Container.scope(user_id)` builds the services for one user."""
from __future__ import annotations

from typing import Optional

from .clock import Clock
from .config import Settings
from .domain.models import User
from .integrations.google.client import GoogleOidcClient
from .integrations.strava.client import HttpTransport, StravaClient
from .integrations.strava.sync_service import StravaSyncService
from .plan.loader import Plan, load_plan
from .services.analysis_service import AnalysisService
from .services.auth_service import AuthService
from .services.checklist_service import ChecklistService
from .services.readiness_service import ReadinessService
from .storage.database import Database
from .storage.repos import (LogRepo, OAuthStateRepo, ReadinessRepo, SessionRepo, StravaRepo, UserRepo,
                            user_for_athlete)


class UserServices:
    """Everything the app needs for one signed-in user. Cheap to build, so it is created per request."""

    def __init__(self, c: "Container", user_id: int):
        self.user_id, self.plan, self.clock, self.settings = user_id, c.plan, c.clock, c.settings
        self.log_repo = LogRepo(c.db, user_id)
        self.readiness_repo = ReadinessRepo(c.db, user_id)
        self.strava_repo = StravaRepo(c.db, user_id)
        self.checklist = ChecklistService(c.plan, self.log_repo, self.readiness_repo, c.clock)
        self.readiness = ReadinessService(self.readiness_repo)
        self.analysis = AnalysisService(c.plan, self.log_repo, self.readiness_repo, self.strava_repo, c.settings, c.clock)
        self.strava = StravaSyncService(c.strava_client, self.strava_repo, c.plan, self.checklist, self.log_repo,
                                        c.clock, c.states, user_id)


class Container:
    def __init__(self, settings: Optional[Settings] = None, http: Optional[HttpTransport] = None,
                 clock: Optional[Clock] = None, plan: Optional[Plan] = None, google_http: Optional[HttpTransport] = None):
        self.settings = s = settings or Settings.from_env()
        self.plan = plan or load_plan(s.plan_path)
        self.clock = clock or Clock(self.plan.meta["utc_offset_hours"])
        self.db = Database(s.db_path)
        self.users, self.sessions, self.states = UserRepo(self.db), SessionRepo(self.db), OAuthStateRepo(self.db)
        self.strava_client = StravaClient(s.strava_client_id, s.strava_client_secret, s.strava_redirect_uri,
                                          s.strava_scope, http)
        self.google = GoogleOidcClient(s.google_client_id, s.google_client_secret, s.google_redirect_uri, google_http)
        self.auth = AuthService(s, self.clock, self.google, self.db, self.users, self.sessions, self.states)

    def scope(self, user_id: int) -> UserServices:
        return UserServices(self, user_id)

    def scope_for_athlete(self, athlete_id: int) -> Optional[UserServices]:
        uid = user_for_athlete(self.db, athlete_id)
        return self.scope(uid) if uid else None

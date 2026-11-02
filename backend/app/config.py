"""Configuration from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: str = str(BASE_DIR / "data" / "training.db")
    plan_path: str = str(BASE_DIR / "app" / "plan" / "seed" / "plan.json")
    frontend_url: str = "http://localhost:4200"
    frontend_dist: str = str(BASE_DIR.parent / "frontend" / "dist" / "training-hub" / "browser")
    cors_origins: tuple[str, ...] = ("http://localhost:4200", "http://localhost:8000")
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_redirect_uri: str = "http://localhost:8000/api/strava/callback"
    strava_verify_token: str = "change-me"
    strava_scope: str = "read,activity:read_all"
    # Google sign-in (OpenID Connect, authorization code + PKCE)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"
    allowed_emails: tuple[str, ...] = ()      # who may sign in; empty = nobody (safe default)
    allow_any_google_user: bool = False       # set true only if anyone with a Google account may use this instance
    session_days: int = 14
    cookie_secure: bool = False               # set true behind HTTPS
    auth_disabled: bool = False               # local development only: skips login and uses a fixed local user
    # Analysis tuning (edit freely)
    max_hr: int = 0                       # 0 = unknown, HR checks are skipped
    easy_run_fastest_s_per_km: int = 355  # faster than 5:55/km on an easy day = "too fast"
    run_load_factor: float = 4.0          # 1 run km counts like 4 ride km in the load score
    gym_minute_load: float = 0.5

    @staticmethod
    def from_env() -> "Settings":
        e = os.environ
        d = Settings()
        return Settings(
            db_path=e.get("TH_DB_PATH", d.db_path),
            plan_path=e.get("TH_PLAN_PATH", d.plan_path),
            frontend_url=e.get("TH_FRONTEND_URL", d.frontend_url),
            frontend_dist=e.get("TH_FRONTEND_DIST", d.frontend_dist),
            cors_origins=tuple(x for x in e.get("TH_CORS_ORIGINS", ",".join(d.cors_origins)).split(",") if x),
            strava_client_id=e.get("STRAVA_CLIENT_ID", ""),
            strava_client_secret=e.get("STRAVA_CLIENT_SECRET", ""),
            strava_redirect_uri=e.get("STRAVA_REDIRECT_URI", d.strava_redirect_uri),
            strava_verify_token=e.get("STRAVA_VERIFY_TOKEN", d.strava_verify_token),
            strava_scope=e.get("STRAVA_SCOPE", d.strava_scope),
            google_client_id=e.get("GOOGLE_CLIENT_ID", ""),
            google_client_secret=e.get("GOOGLE_CLIENT_SECRET", ""),
            google_redirect_uri=e.get("GOOGLE_REDIRECT_URI", d.google_redirect_uri),
            allowed_emails=tuple(x.strip().lower() for x in e.get("TH_ALLOWED_EMAILS", "").split(",") if x.strip()),
            allow_any_google_user=e.get("TH_ALLOW_ANY_GOOGLE_USER", "").lower() in ("1", "true", "yes"),
            session_days=int(e.get("TH_SESSION_DAYS", d.session_days)),
            cookie_secure=e.get("TH_COOKIE_SECURE", "").lower() in ("1", "true", "yes"),
            auth_disabled=e.get("TH_AUTH_DISABLED", "").lower() in ("1", "true", "yes"),
            max_hr=int(e.get("TH_MAX_HR", "0") or 0),
            easy_run_fastest_s_per_km=int(e.get("TH_EASY_RUN_FASTEST", d.easy_run_fastest_s_per_km)),
        )

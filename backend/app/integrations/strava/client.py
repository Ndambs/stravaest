"""Thin Strava API v3 client. HTTP is injected so the logic can be tested offline."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Optional, Protocol

from ...domain.models import StravaActivity
from .mapper import map_sport_type

API = "https://www.strava.com/api/v3"
AUTHORIZE = "https://www.strava.com/oauth/authorize"
TOKEN = "https://www.strava.com/oauth/token"


class StravaError(Exception):
    def __init__(self, status: int, message: str, rate_usage: str | None = None):
        super().__init__(f"Strava {status}: {message}")
        self.status, self.rate_usage = status, rate_usage


class StravaNotConfigured(StravaError):
    def __init__(self):
        super().__init__(503, "Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET (see README)")


class HttpTransport(Protocol):
    def request(self, method: str, url: str, params: dict | None = None, data: dict | None = None,
                headers: dict | None = None) -> tuple[int, Any, dict]: ...


class UrllibTransport:
    def request(self, method, url, params=None, data=None, headers=None):
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        body = urllib.parse.urlencode(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"null"), dict(r.headers)
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b"null")
            except ValueError:
                payload = None
            return e.code, payload, dict(e.headers)


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: int
    athlete_id: Optional[int] = None


class StravaClient:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, scope: str,
                 http: HttpTransport | None = None):
        self.client_id, self.client_secret, self.redirect_uri, self.scope = client_id, client_secret, redirect_uri, scope
        self.http = http or UrllibTransport()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _need_config(self) -> None:
        if not self.configured:
            raise StravaNotConfigured()

    def authorize_url(self, state: str) -> str:
        self._need_config()
        q = urllib.parse.urlencode({"client_id": self.client_id, "redirect_uri": self.redirect_uri,
                                    "response_type": "code", "approval_prompt": "auto",
                                    "scope": self.scope, "state": state})
        return f"{AUTHORIZE}?{q}"

    def _token_call(self, data: dict) -> TokenSet:
        self._need_config()
        status, body, _ = self.http.request("POST", TOKEN, data={"client_id": self.client_id,
                                                                  "client_secret": self.client_secret, **data})
        if status != 200 or not body:
            raise StravaError(status, "token request failed")
        return TokenSet(body["access_token"], body["refresh_token"], int(body["expires_at"]),
                        (body.get("athlete") or {}).get("id"))

    def exchange_code(self, code: str) -> TokenSet:
        return self._token_call({"code": code, "grant_type": "authorization_code"})

    def refresh(self, refresh_token: str) -> TokenSet:
        return self._token_call({"refresh_token": refresh_token, "grant_type": "refresh_token"})

    def _get(self, token: str, path: str, params: dict | None = None) -> Any:
        status, body, headers = self.http.request("GET", API + path, params=params,
                                                  headers={"Authorization": f"Bearer {token}"})
        if status == 429:
            raise StravaError(429, "rate limit reached, try again in 15 minutes", headers.get("X-RateLimit-Usage"))
        if status != 200:
            raise StravaError(status, (body or {}).get("message", "request failed") if isinstance(body, dict) else "request failed")
        return body

    def list_activities(self, token: str, after: int, before: int, per_page: int = 100) -> Iterator[dict]:
        page = 1
        while True:
            batch = self._get(token, "/athlete/activities",
                              {"after": after, "before": before, "page": page, "per_page": per_page})
            if not batch:
                return
            yield from batch
            if len(batch) < per_page:
                return
            page += 1

    def get_activity(self, token: str, activity_id: int) -> dict:
        return self._get(token, f"/activities/{activity_id}")


def to_activity(raw: dict) -> StravaActivity:
    """Strava summary JSON -> domain object. start_date_local is already local wall-clock time (with a 'Z')."""
    sport = raw.get("sport_type") or raw.get("type") or "Workout"
    name = raw.get("name") or ""
    return StravaActivity(
        id=int(raw["id"]), name=name, sport_type=sport,
        start_local=datetime.fromisoformat(raw["start_date_local"].replace("Z", "")),
        distance_m=float(raw.get("distance") or 0), moving_time_s=int(raw.get("moving_time") or 0),
        elapsed_time_s=int(raw.get("elapsed_time") or 0),
        avg_hr=raw.get("average_heartrate"), max_hr=raw.get("max_heartrate"),
        elevation_m=float(raw.get("total_elevation_gain") or 0),
        mapped_type=map_sport_type(sport, name))

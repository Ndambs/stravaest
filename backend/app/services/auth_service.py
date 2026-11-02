"""Sign-in with Google, sessions and the allow-list."""
from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from ..clock import Clock
from ..config import Settings
from ..domain.models import User
from ..integrations.google.client import AuthError, GoogleOidcClient, pkce_challenge
from ..storage.database import Database
from ..storage.repos import OAuthStateRepo, SessionRepo, UserRepo

COOKIE_NAME = "th_session"


def hash_token(token: str) -> str:
    """Only a hash of the session token is stored, so a leaked database cannot be replayed as cookies."""
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, settings: Settings, clock: Clock, google: GoogleOidcClient, db: Database,
                 users: UserRepo, sessions: SessionRepo, states: OAuthStateRepo):
        self.s, self.clock, self.google, self.db = settings, clock, google, db
        self.users, self.sessions, self.states = users, sessions, states

    def _now(self) -> int:
        return int(self.clock.now().timestamp())

    def is_allowed(self, email: str) -> bool:
        return self.s.allow_any_google_user or email.lower() in self.s.allowed_emails

    # ---- Google round trip ----
    def login_url(self) -> str:
        state, nonce, verifier = (secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48))
        url = self.google.authorize_url(state, nonce, pkce_challenge(verifier))   # raises if not configured
        self.states.add("google", state, {"nonce": nonce, "verifier": verifier}, self._now())
        return url

    def complete_login(self, code: str, state: str) -> tuple[User, str]:
        payload = self.states.consume("google", state, self._now())
        if not payload:
            raise AuthError("bad_state", "unknown or expired sign-in attempt")
        tokens = self.google.exchange_code(code, payload["verifier"])
        claims = self.google.validate_id_token(tokens["id_token"], payload["nonce"], self._now())
        email = claims["email"].lower()
        if not self.is_allowed(email):
            raise AuthError("not_allowed", f"{email} is not on the allow-list")
        user = self.users.upsert_google(claims["sub"], email, claims.get("name", ""), claims.get("picture", ""),
                                        self.clock.now().isoformat(timespec="seconds"))
        if self.users.count() == 1 and self.db.has_legacy():
            self.db.claim_legacy(user.id)         # the first person in inherits any pre-login data
        return user, self._new_session(user)

    # ---- sessions ----
    def _new_session(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        now = self._now()
        self.sessions.create(hash_token(token), user.id, now, now + self.s.session_days * 86400)
        return token

    def user_for_token(self, token: Optional[str]) -> Optional[User]:
        if not token:
            return None
        return self.sessions.user_for(hash_token(token), self._now())

    def logout(self, token: Optional[str]) -> None:
        if token:
            self.sessions.delete(hash_token(token))

    # ---- local development ----
    def local_dev_user(self) -> User:
        return self.users.upsert_google("local-dev", "local@localhost", "Local user", "",
                                        self.clock.now().isoformat(timespec="seconds"))

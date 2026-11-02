"""Google sign-in via OpenID Connect: authorization code flow with PKCE (S256). HTTP is injected for testing."""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from typing import Optional

from ..strava.client import HttpTransport, UrllibTransport

AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class AuthError(Exception):
    """`code` is a short machine-readable reason that is safe to show in a redirect URL."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("invalid_token", "malformed ID token")
    try:
        raw = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(raw))
    except (ValueError, json.JSONDecodeError) as e:
        raise AuthError("invalid_token", "unreadable ID token") from e


class GoogleOidcClient:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, http: Optional[HttpTransport] = None):
        self.client_id, self.client_secret, self.redirect_uri = client_id, client_secret, redirect_uri
        self.http = http or UrllibTransport()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authorize_url(self, state: str, nonce: str, code_challenge: str) -> str:
        if not self.configured:
            raise AuthError("not_configured", "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (see README)")
        q = urllib.parse.urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
            "scope": "openid email profile", "state": state, "nonce": nonce,
            "code_challenge": code_challenge, "code_challenge_method": "S256", "prompt": "select_account"})
        return f"{AUTHORIZE}?{q}"

    def exchange_code(self, code: str, code_verifier: str) -> dict:
        status, body, _ = self.http.request("POST", TOKEN, data={
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code", "code_verifier": code_verifier})
        if status != 200 or not isinstance(body, dict) or "id_token" not in body:
            raise AuthError("token_exchange_failed", f"Google token endpoint returned {status}")
        return body

    def validate_id_token(self, id_token: str, nonce: str, now: int) -> dict:
        """Checks the claims. The signature is not re-verified because this token came straight from Google's
        token endpoint over TLS, which OpenID Connect Core 3.1.3.7 allows. Never use this for tokens that
        arrive via the browser."""
        c = decode_jwt_payload(id_token)
        aud = c.get("aud")
        if c.get("iss") not in ISSUERS:
            raise AuthError("invalid_token", "wrong issuer")
        if aud != self.client_id and not (isinstance(aud, list) and self.client_id in aud):
            raise AuthError("invalid_token", "wrong audience")
        if int(c.get("exp", 0)) <= now:
            raise AuthError("invalid_token", "token expired")
        if c.get("nonce") != nonce:
            raise AuthError("invalid_token", "nonce mismatch")
        if not c.get("sub") or not c.get("email"):
            raise AuthError("invalid_token", "missing subject or email")
        if c.get("email_verified") not in (True, "true"):
            raise AuthError("email_not_verified", "Google says this email is not verified")
        return c

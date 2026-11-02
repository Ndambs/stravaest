from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ..container import Container, UserServices
from ..domain.models import User
from ..services.auth_service import COOKIE_NAME


def get_container(request: Request) -> Container:
    return request.app.state.container


def current_user(request: Request, c: Container = Depends(get_container)) -> User:
    if c.settings.auth_disabled:                       # local development only
        return c.auth.local_dev_user()
    user = c.auth.user_for_token(request.cookies.get(COOKIE_NAME))
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def get_scope(user: User = Depends(current_user), c: Container = Depends(get_container)) -> UserServices:
    return c.scope(user.id)


def csrf_guard(request: Request) -> None:
    """Browsers cannot add this header to a cross-site request without a CORS pre-flight, so a forged
    form post from another site is rejected even if the session cookie is sent."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "XMLHttpRequest":
        raise HTTPException(403, "Missing X-Requested-With header")

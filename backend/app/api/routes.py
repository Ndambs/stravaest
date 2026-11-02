"""HTTP layer. Thin on purpose: authenticate, validate, call a service, return JSON."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..container import Container, UserServices
from ..domain.models import User
from ..integrations.google.client import AuthError
from ..integrations.strava.client import StravaError
from ..services.auth_service import COOKIE_NAME
from ..services.checklist_service import ChecklistError
from .deps import csrf_guard, current_user, get_container, get_scope
from .schemas import ChecklistUpdate, ReadinessInput

public = APIRouter(prefix="/api")                                   # no session needed
router = APIRouter(prefix="/api", dependencies=[Depends(csrf_guard)])   # signed-in users only


# ================================================================ public
@public.get("/health")
def health():
    return {"ok": True}


@public.get("/auth/config")
def auth_config(c: Container = Depends(get_container)):
    return {"google_configured": c.google.configured, "auth_disabled": c.settings.auth_disabled}


@public.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"user": asdict(user)}


@public.get("/auth/google/login")
def google_login(c: Container = Depends(get_container)):
    base = c.settings.frontend_url.rstrip("/")
    try:
        return RedirectResponse(c.auth.login_url())
    except AuthError as e:
        return RedirectResponse(f"{base}/login?error={e.code}")


@public.get("/auth/google/callback")
def google_callback(code: Optional[str] = None, state: str = "", error: Optional[str] = None,
                    c: Container = Depends(get_container)):
    base = c.settings.frontend_url.rstrip("/")
    if error or not code:
        return RedirectResponse(f"{base}/login?error={'google_denied' if error else 'missing_code'}")
    try:
        _, token = c.auth.complete_login(code, state)
    except AuthError as e:
        return RedirectResponse(f"{base}/login?error={e.code}")
    resp = RedirectResponse(f"{base}/today")
    resp.set_cookie(COOKIE_NAME, token, max_age=c.settings.session_days * 86400, httponly=True,
                    samesite="lax", secure=c.settings.cookie_secure, path="/")
    return resp


@public.get("/strava/webhook")
def strava_webhook_verify(request: Request, c: Container = Depends(get_container)):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == c.settings.strava_verify_token:
        return {"hub.challenge": q.get("hub.challenge")}
    raise HTTPException(403, "verification failed")


@public.post("/strava/webhook")
async def strava_webhook_event(request: Request, c: Container = Depends(get_container)):
    event = await request.json()
    try:
        scope = c.scope_for_athlete(int(event.get("owner_id", 0)))
        if scope:
            scope.strava.handle_event(event)
    except Exception:  # Strava expects a fast 200; anything missed is picked up by the next sync
        pass
    return {"received": True}


@public.get("/strava/callback")
def strava_callback(request: Request, code: Optional[str] = None, state: str = "", scope: str = "",
                    error: Optional[str] = None, c: Container = Depends(get_container)):
    base = c.settings.frontend_url.rstrip("/")
    user = c.auth.local_dev_user() if c.settings.auth_disabled else c.auth.user_for_token(request.cookies.get(COOKIE_NAME))
    if not user:
        return RedirectResponse(f"{base}/login?error=signin_required")
    if error or not code:
        return RedirectResponse(f"{base}/strava?error={error or 'missing_code'}")
    try:
        c.scope(user.id).strava.handle_callback(code, state, scope)
    except StravaError as e:
        return RedirectResponse(f"{base}/strava?error={e.status}")
    return RedirectResponse(f"{base}/strava?connected=1")


# ================================================================ signed-in users
@router.post("/auth/logout")
def logout(request: Request, c: Container = Depends(get_container)):
    c.auth.logout(request.cookies.get(COOKIE_NAME))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/plan")
def plan(c: Container = Depends(get_container), _: User = Depends(current_user)):
    p = c.plan
    return {"meta": p.meta, "library": p.library,
            "goals": [g.__dict__ for g in p.goals],
            "weeks": [{**w.__dict__, "start": w.start.isoformat(), "end": w.end.isoformat()} for w in p.weeks]}


@router.get("/days")
def days(start: Optional[date] = None, end: Optional[date] = None, s: UserServices = Depends(get_scope)):
    return {"today": s.clock.today().isoformat(),
            "days": s.checklist.range_view(start or s.plan.start, end or s.plan.end)}


@router.get("/days/{day}")
def one_day(day: date, s: UserServices = Depends(get_scope)):
    v = s.checklist.day_view(day)
    if not v:
        raise HTTPException(404, "No plan for that date")
    return v


@router.put("/checklist/{activity_id}")
def set_status(activity_id: str, body: ChecklistUpdate, s: UserServices = Depends(get_scope)):
    try:
        s.checklist.set_status(activity_id, body.status, body.actual_km, body.actual_minutes, body.notes)
    except ChecklistError as e:
        raise HTTPException(404, str(e))
    return s.checklist.day_view(s.plan.activities[activity_id].date)


@router.delete("/checklist/{activity_id}")
def reset_status(activity_id: str, s: UserServices = Depends(get_scope)):
    try:
        s.checklist.reset(activity_id)
    except ChecklistError as e:
        raise HTTPException(404, str(e))
    return s.checklist.day_view(s.plan.activities[activity_id].date)


@router.get("/readiness/{day}")
def get_readiness(day: date, s: UserServices = Depends(get_scope)):
    return s.readiness.get(day) or JSONResponse(None)


@router.put("/readiness/{day}")
def put_readiness(day: date, body: ReadinessInput, s: UserServices = Depends(get_scope)):
    if not s.plan.contains(day):
        raise HTTPException(404, "Date outside the plan")
    return s.readiness.record(day, body.sleep_hours, body.rhr_delta, body.soreness, body.mood)


@router.get("/analysis")
def analysis(as_of: Optional[date] = None, s: UserServices = Depends(get_scope)):
    return s.analysis.analyse(as_of)


@router.get("/strava/status")
def strava_status(s: UserServices = Depends(get_scope)):
    return s.strava.status()


@router.get("/strava/connect")
def strava_connect(s: UserServices = Depends(get_scope)):
    return {"url": s.strava.connect_url()}


@router.post("/strava/sync")
def strava_sync(full: bool = Query(False), s: UserServices = Depends(get_scope)):
    return s.strava.sync(full)


@router.post("/strava/disconnect")
def strava_disconnect(s: UserServices = Depends(get_scope)):
    s.strava.disconnect()
    return {"ok": True}


@router.get("/strava/activities")
def strava_activities(s: UserServices = Depends(get_scope)):
    return s.strava.activities_view()

# Training Hub

Interactive calendar + checklist (with **Google sign-in** and per-user data) for the Nov-Dec 2026 training plan (cycling 1,000 km, running 100 km and walking 110 km a month; Tuesdays are morning-only), with a Python backend and an Angular frontend.
It is built so Strava can feed it: ticked sessions come from your real activities, and an analysis engine tells you
whether you are **on track, ahead, behind, failing or overdoing it**.

```
training-hub/
  backend/    Python 3.10+  (FastAPI + SQLite, domain logic is framework-free)
  frontend/   Angular 19    (standalone components + signals, no UI library)
```

## Quick start

You need Google credentials to sign in (5 minutes, see "Google sign-in" below). To just look around on your own machine,
you can skip that by exporting `TH_AUTH_DISABLED=true` before starting the backend (development only, never on a server).

```bash
# 1. Backend  (http://localhost:8000, docs at /docs)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# 2. Frontend (http://localhost:4200, proxies /api to the backend)
cd ../frontend
npm install
npm start
```

Single-server mode: run `npm run build` in `frontend/`, then start only the backend. It serves
`frontend/dist/training-hub/browser` at `/`.

## What you get

| Screen | What it does |
|---|---|
| **Today** | Date navigation, morning readiness check (green / amber / red + what to change), tick or log every activity, copy the Strava title. |
| **Calendar** | Colour-coded month grid for Nov and Dec. Chips show status (done, done differently, skipped, missed, to do). Tap a day for the full checklist drawer. |
| **Checklist** | Week accordions with filters by activity type, "hide completed", and "Tick all" per day. |
| **Progress** | Verdict banner, goal cards (1,000 km ride / 100 km run / 110 km walk per month) with projection, planned-vs-actual charts and a week table. |
| **Library** | All gym, core and mobility workouts, plus a searchable bank of every Strava title (including Sunday extras). |
| **Strava** | Connect, sync, see what was matched to the plan and what is an unplanned extra. |

## Google sign-in (one time)

1. In the Google Cloud Console create or pick a project, open **APIs & Services > OAuth consent screen**, choose *External*
   and add your Google account under *Test users* (while the app is in "Testing", only test users can sign in).
2. **Credentials > Create credentials > OAuth client ID > Web application.** Add the authorised redirect URI
   `http://localhost:8000/api/auth/google/callback` (add your HTTPS URL too when you deploy).
3. Export before starting the backend (see `backend/.env.example`):
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and **`TH_ALLOWED_EMAILS=you@gmail.com`** (comma-separated).
   With an empty allow-list nobody can sign in; set `TH_ALLOW_ANY_GOOGLE_USER=true` only if anyone with a Google account may use your instance.
4. Open the app, press **Sign in with Google**, then connect Strava from the Strava page (the Strava connection belongs to the person signed in).

How it works: the backend runs the OpenID Connect authorization-code flow with PKCE (S256), a one-time `state` and a `nonce`; it validates the
ID token claims (issuer, audience, expiry, nonce, verified email) and checks your email against the allow-list. It then creates a server-side
session: a random 256-bit token in an HttpOnly, SameSite=Lax cookie (`th_session`); only its SHA-256 hash is stored. Sessions last 14 days
(`TH_SESSION_DAYS`) and sign-out deletes them. Every state-changing API call must carry `X-Requested-With: XMLHttpRequest` (the Angular
interceptor adds it), which blocks forged cross-site requests. The ID token is read straight from Google's token endpoint over TLS, so its
signature is not re-verified (permitted by OpenID Connect Core 3.1.3.7); never reuse that code for tokens that come through the browser.

Each signed-in person gets their own checklist, readiness checks, Strava connection and analysis. The Strava OAuth `state` is tied to the person
who started it, and the Strava webhook finds the owner by athlete id. An older single-user database is upgraded automatically; the first person
to sign in inherits that data.

Deploying behind HTTPS: set `TH_COOKIE_SECURE=true`, `GOOGLE_REDIRECT_URI`, `STRAVA_REDIRECT_URI`, `TH_FRONTEND_URL` and `TH_CORS_ORIGINS`
to your real origin (the simplest setup serves the built frontend from the backend, so everything is one origin).

## Strava setup (one time)

1. Create an API app at <https://www.strava.com/settings/api>. Set "Authorization Callback Domain" to `localhost`.
2. Export the credentials before starting the backend (see `backend/.env.example`):
   `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, and a random `STRAVA_VERIFY_TOKEN`.
3. Open the **Strava** page, press **Connect**, approve (`read,activity:read_all` so private activities are readable), then **Sync now**.

Matching rules (`backend/app/integrations/strava/matcher.py`):
1. A Strava title that equals a title from the title bank (including Tuesday swap titles) is matched to that exact session (within 3 days).
2. Otherwise same day + compatible type (run, ride, walk, gym, rope). Strava `Walk` and `Hike` count as walks. Any other activity fills the optional Sunday finisher slot.
3. A day early or late is accepted only when the distance is within about 20% of the plan.
4. Everything else is an **extra**: it still counts toward km goals and the overdoing checks.
5. Sessions you ticked or skipped by hand are never overwritten. Deleting a Strava activity unticks its session.

Live updates: Strava can push events to `GET/POST /api/strava/webhook`. This needs a public HTTPS URL (for example a tunnel)
and a subscription created with `POST https://www.strava.com/api/v3/push_subscriptions`. Manual sync works without it.
Strava's default limits are 100 requests per 15 minutes and 1,000 per day; a sync uses about one request per 100 activities.

## The plan in one paragraph

Mon: upper gym + easy spin + lunch walk. **Tue (morning only): 6 km run, or full-body weights F1, or rope R1** + short walk.
Wed: lower gym + endurance ride + walk. Thu: easy run + long ride + walk. Fri: interval ride + light prehab + 5 km walk.
Sat: long run (best efforts on 17 Nov, 12 Dec, 19 Dec) + flush spin + walk. Sun: 4 km brisk walk + optional finisher.
Planned totals: Nov 1,075 km ride / 106 km run / 120 km walk; Dec 1,130 / 110 / 123.

## How the analysis works

`backend/app/services/analysis_service.py` (all thresholds are named constants or in `config.py`):

* **Goals**: actual km vs "plan says you should be here by now". Status: Ahead (>= 110%), On track (90-110%), Behind (75-90%),
  Failing (< 75%). Also a projected month-end and the km you now need per remaining session.
* **Overdoing flags**: weekly load > 115% / 130% of plan, deload week not respected, +20% week-on-week ramp, run km > 120% of plan,
  more than 3 hard days or 3 in a row, easy runs faster than 5:55 /km, easy sessions above 80% of max HR (set `TH_MAX_HR`),
  hard Sunday, hard session on a red readiness day.
* **Failing flags**: 2+ key sessions missed in a week, completion < 70% / 50%, run km < 75% two weeks running, 3 training days in a row with nothing done.
* **Load score** = ride km + 4 x run km + 0.5 x gym minutes (a heuristic for comparing weeks, not a physiological measure).
* Past days with nothing ticked and no Strava activity count as missed, so keep the checklist or Strava up to date.

This is training guidance, not medical advice.

## Architecture

```
backend/app
  domain/models.py         dataclasses, no framework imports
  plan/loader.py           loads plan.json (generated from the rota) into an indexed Plan
  storage/                 SQLite database + repositories (the only SQL)
  services/                checklist, readiness, analysis  (pure Python, unit-tested)
  integrations/strava/     client (injectable HTTP), mapper, matcher, sync service
  api/                     FastAPI routes + pydantic schemas (thin)
  container.py             composition root
frontend/src/app
  core/                    models, ApiService, Store (signals), date + theme helpers
  shared/                  chip, progress bar, badge, SVG line chart, activity row, readiness card, day detail
  features/                today, calendar, checklist, dashboard, library, strava (lazy-loaded)
```

To change the plan, edit `backend/app/plan/seed/plan.json` (days, activities, Strava titles, goals) and restart.
Add a new rule by adding a flag in `AnalysisService._flags`, a line in `ACTIONS`, and a test.
Add another data source (Garmin, Apple Health) by writing a sibling of `integrations/strava` that returns `StravaActivity`-shaped objects.

## Tests

```bash
cd backend && python -m unittest discover -s tests -t .
```
43 tests cover the plan totals (Tuesday morning-only, a walk every day), checklist, readiness grading, analysis scenarios (on track, failing, overdoing, deload,
misses, extras), the Strava flow (including walks) and sign-in (PKCE, forged or replayed state, bad tokens, allow-list, session expiry, per-user isolation, old-database upgrade) (OAuth state, sync, matching, deletion) using a fake HTTP transport, so no network is needed.

## Security notes

* Strava tokens are stored unencrypted in `backend/data/training.db`; protect that file, or add encryption at rest before hosting it for other people.
* Keep `GOOGLE_CLIENT_SECRET`, `STRAVA_CLIENT_SECRET` and `STRAVA_VERIFY_TOKEN` out of git (`.env` is ignored).
* `TH_AUTH_DISABLED=true` removes login entirely: use it only on your own laptop.
* CORS allows only `localhost:4200` and `localhost:8000` by default (`TH_CORS_ORIGINS`), with credentials.
* There is no rate limiting or account recovery: sign-in is delegated to Google and gated by the allow-list.

## Status of this codebase

The backend domain, services, storage, sign-in and Strava logic were run against the 43 tests above (Google and Strava were replaced by fake HTTP, so no real Google login was performed). The FastAPI route layer (including the Google callback and cookie handling) and the
Angular app were written carefully but not executed in the environment that produced them (no network for `pip` / `npm`), so expect to
fix small compile or typing issues on first run. `strictTemplates` is off in `frontend/tsconfig.json`; turn it on once it builds cleanly.

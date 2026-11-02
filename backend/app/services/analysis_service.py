"""Are we on track, overdoing it, or failing?  Pure logic over plan + logs + Strava + readiness.

Everything here is a heuristic with named thresholds so you can tune it. It is guidance, not medical advice.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from ..clock import Clock
from ..config import Settings
from ..domain.models import COUNTED, DISTANCE_TYPES, GYM, RIDE, RUN, WALK, LogEntry, PlannedActivity, StravaActivity
from ..plan.loader import Plan
from ..storage.repos import LogRepo, ReadinessRepo, StravaRepo

SEV = {"low": 1, "medium": 2, "high": 3}

ACTIONS = {
    "LOAD_SPIKE": "Cut this week's remaining spins by a third, drop one interval rep, and keep Thursday's ride strictly easy.",
    "DELOAD_NOT_RESPECTED": "This is a deload week: stop at the planned km. The adaptation happens when you recover.",
    "WEEKLY_RAMP": "Weekly load jumped by more than 20%. Hold next week at this level instead of adding more.",
    "RUN_VOLUME_HIGH": "Run volume is well above plan. Replace the next easy run with a spin or a rest day to protect shins and Achilles.",
    "HARD_DAY_BUDGET": "Keep to three hard days a week (Tue run, Fri interval ride, Sat long run). Make the extras easy or drop them.",
    "EASY_TOO_FAST": "Your easy runs are too quick. Slow to 6:20-6:50 /km (or by heart rate) so the hard days stay hard.",
    "HR_TOO_HIGH_EASY": "Heart rate on easy days is high. Slow down or check sleep and hydration.",
    "SUNDAY_TOO_HARD": "Sunday is recovery: 30-45 min at RPE 3, no long rides or runs.",
    "TRAINED_THROUGH_RED": "You trained hard on a red readiness day. Follow the red-day swap next time.",
    "KEY_SESSIONS_MISSED": "Protect the key sessions first (Tue quality run, Fri interval ride, Sat long run). Move them one day rather than skipping.",
    "LOW_COMPLETION": "Trim the plan to the essentials this week: key sessions, one gym, and the long ride.",
    "RUN_VOLUME_LOW": "Run volume is behind. Add 10-15 minutes to the next two easy runs instead of one big catch-up run.",
    "CONSECUTIVE_MISSES": "Three days with nothing logged. Start with an easy 30 min spin or a walk today to restart the routine.",
    "NO_DATA": "Log activities by ticking them off, or connect Strava, so the analysis has something to read.",
}


def load_score(ride_km: float, run_km: float, gym_minutes: float, s: Settings) -> float:
    return ride_km + s.run_load_factor * run_km + s.gym_minute_load * gym_minutes


@dataclass
class Actual:
    date: date
    type: str
    km: float
    minutes: int
    activity_id: Optional[str]
    planned: Optional[PlannedActivity]
    pace_s_per_km: Optional[float] = None
    avg_hr: Optional[float] = None
    extra: bool = False


def collect_actuals(plan: Plan, logs: dict[str, LogEntry], strava: list[StravaActivity]) -> list[Actual]:
    by_id = {s.id: s for s in strava}
    out: list[Actual] = []
    for aid, e in logs.items():
        a = plan.activities.get(aid)
        if not a or e.status not in COUNTED:
            continue
        s = by_id.get(e.strava_id) if e.strava_id else None
        typ = s.mapped_type if s else a.type
        if e.actual_km is not None:
            km = e.actual_km
        elif s and typ in DISTANCE_TYPES:
            km = s.km
        else:
            km = a.planned_km if typ == a.type and typ in DISTANCE_TYPES else 0.0
        minutes = e.actual_minutes or (s.minutes if s else a.planned_minutes)
        out.append(Actual(s.day if s else a.date, typ, km if typ in DISTANCE_TYPES else 0.0, minutes, aid, a,
                          s.pace_s_per_km if s and typ == RUN else None, s.avg_hr if s else None))
    for s in strava:
        if s.matched_activity_id is None and plan.contains(s.day):
            out.append(Actual(s.day, s.mapped_type, s.km if s.mapped_type in DISTANCE_TYPES else 0.0, s.minutes,
                              None, None, s.pace_s_per_km if s.mapped_type == RUN else None, s.avg_hr, True))
    return out


class AnalysisService:
    def __init__(self, plan: Plan, logs: LogRepo, readiness: ReadinessRepo, strava: StravaRepo,
                 settings: Settings, clock: Clock):
        self.plan, self.logs, self.readiness, self.strava, self.s, self.clock = plan, logs, readiness, strava, settings, clock

    # ------------------------------------------------------------------ public
    def analyse(self, as_of: Optional[date] = None) -> dict:
        as_of = as_of or self.clock.today()
        logs, rd, strava = self.logs.all(), self.readiness.all(), self.strava.activities()
        actuals = [a for a in collect_actuals(self.plan, logs, strava) if a.date <= as_of]

        def resolved(a: PlannedActivity) -> bool:
            return a.date < as_of or a.id in logs

        weeks = [self._week(w, as_of, logs, actuals, resolved, rd) for w in self.plan.weeks if w.start <= as_of]
        goals = [self._goal(g, as_of, actuals, resolved) for g in self.plan.goals]
        flags = self._flags(weeks, as_of, logs, actuals, resolved, rd)
        data_quality = self._data_quality(as_of, logs, strava)
        verdict, headline = self._verdict(as_of, weeks, goals, flags, data_quality)
        actions = []
        for f in sorted(flags, key=lambda f: -SEV[f["severity"]]):
            t = ACTIONS.get(f["code"])
            if t and t not in actions:
                actions.append(t)
        for g in goals:
            if g["status"] in ("BEHIND", "FAILING") and g["required_per_session"]:
                actions.append(f"To hit the {g['month']} {g['type']} goal you now need about "
                               f"{g['required_per_session']:.0f} km per remaining {g['type']} session.")
        return {"as_of": as_of.isoformat(), "verdict": verdict, "headline": headline, "flags": flags,
                "actions": actions[:6], "goals": goals, "weeks": weeks, "data_quality": data_quality}

    # ------------------------------------------------------------------ pieces
    def _week(self, w, as_of, logs, actuals, resolved, rd) -> dict:
        end_excl = min(as_of, w.end + timedelta(days=1))
        elapsed = max(0, (end_excl - w.start).days)
        acts = [a for a in self.plan.activities_between(w.start, w.end) if not a.optional and resolved(a)]
        done = [a for a in acts if a.id in logs and logs[a.id].status in COUNTED]
        key_p = [a for a in acts if a.key]
        key_d = [a for a in key_p if a in done]
        wa = [x for x in actuals if w.start <= x.date <= w.end]
        ride = sum(x.km for x in wa if x.type == RIDE)
        run = sum(x.km for x in wa if x.type == RUN)
        gym_min = sum(x.minutes for x in wa if x.type == GYM)
        walk = sum(x.km for x in wa if x.type == WALK)
        p_walk = sum(a.planned_km for a in acts if a.type == WALK)
        p_ride = sum(a.planned_km for a in acts if a.type == RIDE)
        p_run = sum(a.planned_km for a in acts if a.type == RUN)
        p_gym = sum(a.planned_minutes for a in acts if a.type == GYM)
        hard = sorted({x.date for x in wa if (x.planned and x.planned.hard)
                       or (x.extra and ((x.type == RUN and x.km >= 5) or (x.type == RIDE and x.km >= 60)))})
        p_load = load_score(p_ride, p_run, p_gym, self.s)
        a_load = load_score(ride, run, gym_min, self.s)
        return {
            "n": w.n, "start": w.start.isoformat(), "end": w.end.isoformat(), "title": w.title, "deload": w.deload,
            "complete": as_of > w.end, "elapsed_days": elapsed,
            "planned": {"ride_km": round(p_ride, 1), "run_km": round(p_run, 1), "walk_km": round(p_walk, 1), "gym_min": p_gym, "load": round(p_load)},
            "actual": {"ride_km": round(ride, 1), "run_km": round(run, 1), "walk_km": round(walk, 1), "gym_min": gym_min, "load": round(a_load)},
            "load_ratio": round(a_load / p_load, 2) if p_load else None,
            "completion": round(len(done) / len(acts), 2) if acts else None,
            "key_planned": len(key_p), "key_done": len(key_d),
            "hard_days": [d.isoformat() for d in hard], "max_consecutive_hard": _max_run_of_days(hard),
        }

    def _goal(self, g, as_of, actuals, resolved) -> dict:
        y, m = map(int, g.month.split("-"))
        first = date(y, m, 1)
        last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        month_acts = [a for a in self.plan.activities_between(first, last) if a.type == g.type]
        planned_total = sum(a.planned_km for a in month_acts)
        planned_to_date = sum(a.planned_km for a in month_acts if resolved(a))
        remaining = [a for a in month_acts if not resolved(a)]
        actual = sum(x.km for x in actuals if x.type == g.type and first <= x.date <= last)
        adherence = actual / planned_to_date if planned_to_date > 0 else None
        factor = min(1.25, max(0.5, adherence)) if adherence is not None else 1.0
        projected = actual + sum(a.planned_km for a in remaining) * factor
        need = max(0.0, g.target - actual)
        per_session = need / len(remaining) if remaining else None
        if actual >= g.target:
            status = "ACHIEVED"
        elif as_of < first or planned_to_date == 0:
            status = "NOT_STARTED"
        elif as_of > last:
            status = "MISSED"
        elif adherence is not None and adherence >= 1.10:
            status = "AHEAD"
        elif adherence is not None and adherence >= 0.90:
            status = "ON_TRACK"
        elif adherence is not None and adherence >= 0.75:
            status = "BEHIND"
        else:
            status = "FAILING"
        if status in ("ON_TRACK", "AHEAD") and projected < g.target:
            status = "BEHIND"
        series, p_cum, a_cum, d = [], 0.0, 0.0, first
        by_day_p, by_day_a = {}, {}
        for a in month_acts:
            by_day_p[a.date] = by_day_p.get(a.date, 0) + a.planned_km
        for x in actuals:
            if x.type == g.type:
                by_day_a[x.date] = by_day_a.get(x.date, 0) + x.km
        while d <= last:
            p_cum += by_day_p.get(d, 0)
            a_cum += by_day_a.get(d, 0)
            series.append({"date": d.isoformat(), "planned": round(p_cum, 1),
                           "actual": round(a_cum, 1) if d <= as_of else None})
            d += timedelta(days=1)
        return {"id": g.id, "month": g.month, "type": g.type, "target": g.target, "actual": round(actual, 1),
                "planned_to_date": round(planned_to_date, 1), "planned_total": round(planned_total, 1),
                "delta_vs_plan": round(actual - planned_to_date, 1),
                "adherence": round(adherence, 2) if adherence is not None else None,
                "projected": round(projected, 1), "remaining_sessions": len(remaining),
                "required_per_session": round(per_session, 1) if per_session is not None else None,
                "status": status, "series": series}

    def _flags(self, weeks, as_of, logs, actuals, resolved, rd) -> list[dict]:
        flags: list[dict] = []

        def add(code, severity, msg, week=None):
            flags.append({"code": code, "kind": "failing" if code in FAILING else "overdoing" if code in OVERDOING else "info",
                          "severity": severity, "message": msg, "week": week})

        recent = weeks[-2:]
        for i, w in enumerate(recent):
            prev = weeks[weeks.index(w) - 1] if weeks.index(w) > 0 else None
            enough = w["complete"] or w["elapsed_days"] >= 3
            r, n = w["load_ratio"], w["n"]
            if enough and r is not None:
                if w["deload"] and r >= 1.10:
                    add("DELOAD_NOT_RESPECTED", "high" if r >= 1.25 else "medium",
                        f"Week {n} is a deload week but you did {round(r * 100)}% of the planned load.", n)
                elif r >= 1.30:
                    add("LOAD_SPIKE", "high", f"Week {n}: load is {round(r * 100)}% of plan.", n)
                elif r >= 1.15:
                    add("LOAD_SPIKE", "medium", f"Week {n}: load is {round(r * 100)}% of plan.", n)
                pl = w["planned"]["run_km"]
                if pl > 0 and w["actual"]["run_km"] >= 1.2 * pl:
                    add("RUN_VOLUME_HIGH", "high" if w["actual"]["run_km"] >= 1.4 * pl else "medium",
                        f"Week {n}: {w['actual']['run_km']} km run vs {pl} km planned.", n)
                if prev and not prev["deload"] and prev["actual"]["load"] > 0 and r > 1.0 \
                        and w["actual"]["load"] >= 1.20 * prev["actual"]["load"]:
                    add("WEEKLY_RAMP", "medium", f"Week {n}: load is up {round((w['actual']['load'] / prev['actual']['load'] - 1) * 100)}% on last week.", n)
            if len(w["hard_days"]) > 3:
                add("HARD_DAY_BUDGET", "medium", f"Week {n}: {len(w['hard_days'])} hard days (budget is 3).", n)
            if w["max_consecutive_hard"] >= 3:
                add("HARD_DAY_BUDGET", "high", f"Week {n}: {w['max_consecutive_hard']} hard days in a row.", n)
            if enough:
                if w["completion"] is not None:
                    if w["completion"] < 0.5:
                        add("LOW_COMPLETION", "high", f"Week {n}: only {round(w['completion'] * 100)}% of sessions done.", n)
                    elif w["completion"] < 0.7:
                        add("LOW_COMPLETION", "medium", f"Week {n}: {round(w['completion'] * 100)}% of sessions done.", n)
                missed = w["key_planned"] - w["key_done"]
                if missed >= 2:
                    add("KEY_SESSIONS_MISSED", "high", f"Week {n}: {missed} key sessions missed.", n)
                elif missed == 1 and w["complete"]:
                    add("KEY_SESSIONS_MISSED", "low", f"Week {n}: one key session missed.", n)
        done_weeks = [w for w in weeks if w["complete"] and w["planned"]["run_km"] > 0]
        if len(done_weeks) >= 2 and all(w["actual"]["run_km"] < 0.75 * w["planned"]["run_km"] for w in done_weeks[-2:]):
            add("RUN_VOLUME_LOW", "medium", "Run km below 75% of plan two weeks running.", done_weeks[-1]["n"])

        # per-activity checks
        easy_runs = [x for x in actuals if x.type == RUN and x.planned and x.planned.intensity == "easy"
                     and x.pace_s_per_km and x.date > as_of - timedelta(days=14)]
        fast = [x for x in easy_runs if x.pace_s_per_km < self.s.easy_run_fastest_s_per_km]
        if fast:
            add("EASY_TOO_FAST", "medium" if len(fast) >= 2 else "low",
                f"{len(fast)} easy run(s) faster than {_pace(self.s.easy_run_fastest_s_per_km)} /km recently.")
        if self.s.max_hr:
            hot = [x for x in actuals if x.avg_hr and x.planned and x.planned.intensity in ("easy", "light")
                   and x.avg_hr > 0.80 * self.s.max_hr and x.date > as_of - timedelta(days=14)]
            if hot:
                add("HR_TOO_HIGH_EASY", "medium" if len(hot) >= 2 else "low",
                    f"{len(hot)} easy session(s) above 80% of max heart rate.")
        for x in actuals:
            if x.date.weekday() == 6 and x.date > as_of - timedelta(days=14) and (
                    (x.type == RUN and x.km >= 8) or (x.type == RIDE and x.km >= 25) or x.minutes > 75):
                add("SUNDAY_TOO_HARD", "medium", f"Sunday {x.date.isoformat()}: {x.type} {x.km:.0f} km / {x.minutes} min.")
                break
        reds = [d for d, r in rd.items() if r.result == "red" and as_of - timedelta(days=7) <= d <= as_of]
        pushed = [x for x in actuals if x.date in reds and x.planned and x.planned.hard]
        if pushed:
            add("TRAINED_THROUGH_RED", "medium", f"Hard session on {len(pushed)} red readiness day(s).")

        # consecutive misses, walking back from yesterday
        streak, d = 0, as_of - timedelta(days=1)
        while d >= self.plan.start:
            day = self.plan.day(d)
            counted = [a for a in (day.activities if day else []) if not a.optional]
            if counted:
                if any(a.id in logs and logs[a.id].status in COUNTED for a in counted):
                    break
                streak += 1
            d -= timedelta(days=1)
        if streak >= 3:
            add("CONSECUTIVE_MISSES", "high", f"{streak} training days in a row with nothing completed.")
        return _dedupe(flags)

    def _data_quality(self, as_of, logs, strava) -> dict:
        last3 = [a for a in self.plan.activities_between(max(self.plan.start, as_of - timedelta(days=3)), as_of - timedelta(days=1))]
        logged = [a for a in last3 if a.id in logs]
        return {"entries_logged": len(logs), "strava_activities": len(strava),
                "no_recent_data": bool(last3) and not logged and not strava,
                "note": "Without ticked sessions or a Strava sync, past days count as missed."}

    def _verdict(self, as_of, weeks, goals, flags, dq) -> tuple[str, str]:
        if as_of <= self.plan.start or not weeks:
            return "NOT_STARTED", "The plan starts on Mon 2 Nov. Nothing to analyse yet."
        if dq["entries_logged"] == 0 and dq["strava_activities"] == 0 and as_of > self.plan.start + timedelta(days=2):
            flags.append({"code": "NO_DATA", "kind": "info", "severity": "low", "week": None,
                          "message": "No activities logged yet."})
            return "NO_DATA", "No activity data yet: tick sessions off or connect Strava."
        over = [f for f in flags if f["kind"] == "overdoing"]
        fail = [f for f in flags if f["kind"] == "failing"]
        if any(f["severity"] == "high" for f in over) or sum(1 for f in over if SEV[f["severity"]] >= 2) >= 2:
            return "OVERDOING", "You are doing more than the plan. Fatigue and injury risk is rising: ease off."
        if any(f["severity"] == "high" for f in fail) or any(g["status"] == "FAILING" for g in goals):
            return "FAILING", "You are well behind the plan. Simplify the week and protect the key sessions."
        completions = [w["completion"] for w in weeks if w["completion"] is not None]
        if any(g["status"] == "BEHIND" for g in goals) or any(SEV[f["severity"]] >= 2 for f in fail) \
                or (completions and completions[-1] < 0.85):
            return "BEHIND", "Slightly behind plan. A small catch-up on the long ride and long run gets you back."
        if any(g["status"] == "AHEAD" for g in goals) and not over:
            return "AHEAD", "Ahead of plan. Bank the buffer: do not add intensity."
        return "ON_TRACK", "On track. Keep the easy days easy."


OVERDOING = {"LOAD_SPIKE", "DELOAD_NOT_RESPECTED", "WEEKLY_RAMP", "RUN_VOLUME_HIGH", "HARD_DAY_BUDGET",
             "EASY_TOO_FAST", "HR_TOO_HIGH_EASY", "SUNDAY_TOO_HARD", "TRAINED_THROUGH_RED"}
FAILING = {"KEY_SESSIONS_MISSED", "LOW_COMPLETION", "RUN_VOLUME_LOW", "CONSECUTIVE_MISSES"}


def _pace(s: int) -> str:
    return f"{s // 60}:{s % 60:02d}"


def _max_run_of_days(days: list[date]) -> int:
    best = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if prev and (d - prev).days == 1 else 1
        best, prev = max(best, run), d
    return best


def _dedupe(flags: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for f in flags:
        k = (f["code"], f["week"])
        if k not in seen or SEV[f["severity"]] > SEV[seen[k]["severity"]]:
            seen[k] = f
    return sorted(seen.values(), key=lambda f: -SEV[f["severity"]])

import unittest
from datetime import date

from tests.helpers import make_container, tick_all


def codes(res):
    return {f["code"] for f in res["flags"]}


class AnalysisTests(unittest.TestCase):
    def test_before_start(self):
        c = make_container(date(2026, 10, 30))
        self.assertEqual(c.analysis.analyse()["verdict"], "NOT_STARTED")

    def test_no_data_midweek(self):
        c = make_container(date(2026, 11, 6))
        self.assertEqual(c.analysis.analyse()["verdict"], "NO_DATA")

    def test_perfect_adherence_is_on_track(self):
        c = make_container(date(2026, 11, 15))
        tick_all(c, date(2026, 11, 2), date(2026, 11, 14))
        r = c.analysis.analyse()
        self.assertEqual(r["verdict"], "ON_TRACK", r["flags"])
        g = {x["id"]: x for x in r["goals"]}
        self.assertEqual(g["ride-2026-11"]["actual"], 535.0)
        self.assertEqual(g["run-2026-11"]["actual"], 57.0)
        self.assertEqual(g["walk-2026-11"]["actual"], 52.0)       # 6 walks + Sunday + 6 walks
        self.assertEqual(g["ride-2026-11"]["status"], "ON_TRACK")
        self.assertGreater(g["ride-2026-11"]["projected"], 1000)

    def test_skipping_most_sessions_is_failing(self):
        c = make_container(date(2026, 11, 15))
        # week 1 fully done, week 2: only the Monday spin
        tick_all(c, date(2026, 11, 2), date(2026, 11, 7))
        c.checklist.set_status("2026-11-09-ride", "done", 30)
        r = c.analysis.analyse()
        self.assertEqual(r["verdict"], "FAILING", (r["verdict"], r["flags"]))
        self.assertIn("KEY_SESSIONS_MISSED", codes(r))
        self.assertTrue(r["actions"])

    def test_extra_volume_is_overdoing(self):
        c = make_container(date(2026, 11, 8))
        tick_all(c, date(2026, 11, 2), date(2026, 11, 7), factor=1.5)   # 50% more km everywhere
        r = c.analysis.analyse()
        self.assertEqual(r["verdict"], "OVERDOING", r["flags"])
        self.assertIn("LOAD_SPIKE", codes(r))
        self.assertIn("RUN_VOLUME_HIGH", codes(r))

    def test_deload_week_overdone(self):
        c = make_container(date(2026, 11, 29))
        tick_all(c, date(2026, 11, 23), date(2026, 11, 28), factor=1.3)
        self.assertIn("DELOAD_NOT_RESPECTED", codes(c.analysis.analyse()))

    def test_three_misses_in_a_row(self):
        c = make_container(date(2026, 11, 6))
        c.checklist.set_status("2026-11-02-ride", "done", 30)   # only data point is Monday
        r = c.analysis.analyse()
        self.assertIn("CONSECUTIVE_MISSES", codes(r))

    def test_goal_series_and_achieved(self):
        c = make_container(date(2026, 12, 1))
        tick_all(c, date(2026, 11, 2), date(2026, 11, 30))
        g = {x["id"]: x for x in c.analysis.analyse()["goals"]}
        self.assertEqual(g["ride-2026-11"]["status"], "ACHIEVED")
        self.assertEqual(len(g["ride-2026-11"]["series"]), 30)

    def test_unplanned_extra_counts_towards_goal(self):
        from datetime import datetime
        from app.domain.models import StravaActivity
        c = make_container(date(2026, 11, 8))
        tick_all(c, date(2026, 11, 2), date(2026, 11, 7))
        base = c.analysis.analyse()["goals"][0]["actual"]
        c.strava_repo.upsert_activity(StravaActivity(1, "Evening spin", "Ride", datetime(2026, 11, 7, 17, 0), 20000, 3000, mapped_type="ride"))
        self.assertEqual(c.analysis.analyse()["goals"][0]["actual"], base + 20)


if __name__ == "__main__":
    unittest.main()

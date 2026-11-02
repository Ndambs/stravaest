import unittest
from datetime import date

from app.services.checklist_service import ChecklistError
from tests.helpers import make_container


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.c = make_container(date(2026, 11, 10))

    def test_plan_totals_match_rota(self):
        p = self.c.plan
        def total(t, m):
            return sum(a.planned_km for a in p.activities.values() if a.type == t and a.date.month == m)
        self.assertEqual(total("ride", 11), 1075)
        self.assertEqual(total("run", 11), 106)
        self.assertEqual(total("walk", 11), 120)
        self.assertEqual(total("ride", 12), 1130)
        self.assertEqual(total("run", 12), 110)
        self.assertEqual(total("walk", 12), 123)
        self.assertEqual(len(p.days), 61)
        self.assertEqual({(g.type, g.target) for g in p.goals}, {("ride", 1000.0), ("run", 100.0), ("walk", 110.0)})

    def test_tuesdays_are_morning_only(self):
        for d, day in self.c.plan.days.items():
            if d.weekday() == 1:
                types = {a.type for a in day.activities}
                self.assertNotIn("ride", types, d)                       # no evening ride on Tuesdays
                self.assertTrue(types & {"run", "gym"}, d)               # a morning run or weights
        nov24 = [a.code for a in self.c.plan.day(date(2026, 11, 24)).activities if a.type == "gym"]
        self.assertEqual(nov24, ["F1"])
        for d in (date(2026, 11, 3), date(2026, 11, 10), date(2026, 12, 1)):
            run = next(a for a in self.c.plan.day(d).activities if a.type == "run")
            self.assertEqual(run.planned_km, 6.0)

    def test_every_day_has_a_walk(self):
        for d, day in self.c.plan.days.items():
            self.assertEqual(sum(1 for a in day.activities if a.type == "walk"), 1, d)

    def test_every_sunday_has_a_walk_and_an_optional_finisher_only(self):
        for d, day in self.c.plan.days.items():
            if d.weekday() == 6:
                self.assertEqual(sorted(a.type for a in day.activities), ["recovery", "walk"], d)

    def test_strava_titles_unique(self):
        titles = [a.strava_title for a in self.c.plan.activities.values() if a.strava_title]
        self.assertEqual(len(titles), len(set(titles)))

    def test_set_and_reset(self):
        self.c.checklist.set_status("2026-11-02-ride", "done", 30)
        v = self.c.checklist.day_view(date(2026, 11, 2))
        ride = next(a for a in v["activities"] if a["type"] == "ride")
        self.assertEqual(ride["status"], "done")
        self.c.checklist.reset("2026-11-02-ride")
        v = self.c.checklist.day_view(date(2026, 11, 2))
        ride = next(a for a in v["activities"] if a["type"] == "ride")
        self.assertEqual(ride["status"], "missed")   # past day with nothing logged

    def test_future_is_pending_and_big_difference_is_modified(self):
        v = self.c.checklist.day_view(date(2026, 11, 20))
        self.assertTrue(all(a["status"] == "pending" for a in v["activities"]))
        e = self.c.checklist.set_status("2026-11-02-ride", "done", 15)   # half the planned 30 km
        self.assertEqual(e.status, "modified")

    def test_validation(self):
        with self.assertRaises(ChecklistError):
            self.c.checklist.set_status("nope", "done")
        with self.assertRaises(ChecklistError):
            self.c.checklist.set_status("2026-11-02-ride", "flying")


if __name__ == "__main__":
    unittest.main()

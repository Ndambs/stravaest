import unittest
from datetime import date

from app.services.readiness_service import grade
from tests.helpers import make_container


class ReadinessTests(unittest.TestCase):
    def test_grades(self):
        self.assertEqual(grade(8, 0, 2, "good")[0], "green")
        self.assertEqual(grade(6.5, 0, 2, "good")[0], "amber")
        self.assertEqual(grade(5, 0, 2, "good")[0], "red")
        self.assertEqual(grade(6.5, 5, 5, "good")[0], "red")      # 3 ambers = red
        self.assertEqual(grade(8, 0, 8, "good")[0], "red")

    def test_round_trip(self):
        c = make_container(date(2026, 11, 5))
        out = c.readiness.record(date(2026, 11, 5), 6.5, 2, 3, "good")
        self.assertEqual(out["result"], "amber")
        self.assertEqual(c.readiness.get(date(2026, 11, 5))["result"], "amber")
        v = c.checklist.day_view(date(2026, 11, 5))
        self.assertEqual(v["readiness"]["result"], "amber")


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import date

from app.integrations.strava.client import StravaError, to_activity
from app.integrations.strava.mapper import map_sport_type
from tests.helpers import make_container


class FakeHttp:
    """Records calls and replays canned Strava responses."""
    def __init__(self, activities=None):
        self.calls, self.activities = [], activities or []

    def request(self, method, url, params=None, data=None, headers=None):
        self.calls.append((method, url, params, data, headers))
        if url.endswith("/oauth/token"):
            return 200, {"access_token": "AT", "refresh_token": "RT", "expires_at": 4102444800, "athlete": {"id": 138526058}}, {}
        if "/athlete/activities" in url:
            page = (params or {}).get("page", 1)
            return 200, (self.activities if page == 1 else []), {}
        return 404, {"message": "nope"}, {}


def raw(i, name, sport, day, km, mins, hr=None):
    return {"id": i, "name": name, "sport_type": sport, "start_date_local": f"{day}T06:00:00Z",
            "distance": km * 1000, "moving_time": mins * 60, "elapsed_time": mins * 60,
            "average_heartrate": hr, "total_elevation_gain": 10}


class StravaTests(unittest.TestCase):
    def connect(self, http, today=date(2026, 11, 10)):
        c = make_container(today, http=http)
        url = c.strava.connect_url()
        state = url.split("state=")[1].split("&")[0]
        c.strava.handle_callback("CODE", state, "read,activity:read_all")
        return c

    def test_mapper(self):
        self.assertEqual(map_sport_type("TrailRun", "x"), "run")
        self.assertEqual(map_sport_type("Walk", "x"), "walk")
        self.assertEqual(map_sport_type("Hike", "x"), "walk")
        self.assertEqual(map_sport_type("Swim", "x"), "recovery")
        self.assertEqual(map_sport_type("GravelRide", "x"), "ride")
        self.assertEqual(map_sport_type("WeightTraining", "x"), "gym")
        self.assertEqual(map_sport_type("Workout", "Jump rope session"), "rope")
        self.assertEqual(map_sport_type("Workout", "Heartbeat in Morse Code", "2026-11-02-rope"), "rope")

    def test_oauth_state_is_checked_and_single_use(self):
        c = make_container(date(2026, 11, 10), http=FakeHttp())
        with self.assertRaises(StravaError):
            c.strava.handle_callback("CODE", "forged", "")
        state = c.strava.connect_url().split("state=")[1].split("&")[0]
        c.strava.handle_callback("CODE", state, "")
        with self.assertRaises(StravaError):
            c.strava.handle_callback("CODE", state, "")      # replay

    def test_sync_matches_by_title_and_by_day_and_flags_extras(self):
        acts = [
            raw(1, "Shooting Star, No Wish Needed", "Ride", "2026-11-02", 30.4, 75),       # title match (Mon ride)
            raw(2, "Morning run", "Run", "2026-11-03", 9.2, 55, hr=150),                    # same-day run match
            raw(3, "Late spin", "Ride", "2026-11-04", 12, 30),                              # Wed ride planned 35 km -> still same-day match
            raw(4, "Extra spin", "Ride", "2026-11-04", 15, 40),                             # second ride that day -> extra
        ]
        c = self.connect(FakeHttp(acts))
        out = c.strava.sync()
        self.assertEqual(out["fetched"], 4)
        self.assertEqual(out["matched"], 3)
        self.assertEqual(out["unplanned"], 1)
        logs = c.log_repo.all()
        self.assertEqual(logs["2026-11-02-ride"].source, "strava")
        self.assertEqual(logs["2026-11-02-ride"].status, "done")
        self.assertEqual(logs["2026-11-03-run"].actual_km, 9.2)
        self.assertEqual(logs["2026-11-04-ride"].status, "modified")                     # 12 km vs 35 km planned
        # the unplanned ride still counts towards the cycling goal
        g = next(x for x in c.analysis.analyse()["goals"] if x["id"] == "ride-2026-11")
        self.assertAlmostEqual(g["actual"], 30.4 + 12 + 15, places=1)

    def test_manual_log_is_never_overwritten(self):
        c = self.connect(FakeHttp([raw(1, "Ride", "Ride", "2026-11-02", 30, 75)]))
        c.checklist.set_status("2026-11-02-ride", "skipped", notes="rain")
        c.strava.sync()
        self.assertEqual(c.log_repo.all()["2026-11-02-ride"].status, "skipped")

    def test_sunday_slot_accepts_any_light_activity(self):
        c = self.connect(FakeHttp([raw(9, "Parkrun", "Run", "2026-11-08", 5, 30)]), today=date(2026, 11, 9))
        c.strava.sync()
        e = c.log_repo.all()["2026-11-08-recovery"]
        self.assertEqual(e.strava_id, 9)
        run = next(x for x in c.analysis.analyse()["goals"] if x["id"] == "run-2026-11")
        self.assertEqual(run["actual"], 5.0)     # a Sunday run still counts as running km

    def test_walks_match_and_count_towards_the_walking_goal(self):
        acts = [raw(1, "Lunch loop", "Walk", "2026-11-02", 4.3, 48), raw(2, "Hill hike", "Hike", "2026-11-08", 5.1, 70)]
        c = self.connect(FakeHttp(acts), today=date(2026, 11, 9))
        c.strava.sync()
        logs = c.log_repo.all()
        self.assertEqual(logs["2026-11-02-walk"].actual_km, 4.3)
        self.assertEqual(logs["2026-11-08-walk"].strava_id, 2)      # Sunday walk, not the finisher slot
        g = next(x for x in c.analysis.analyse()["goals"] if x["id"] == "walk-2026-11")
        self.assertAlmostEqual(g["actual"], 9.4, places=1)

    def test_deleted_strava_activity_unticks(self):
        http = FakeHttp([raw(1, "Ride", "Ride", "2026-11-02", 30, 75)])
        c = self.connect(http)
        c.strava.sync()
        self.assertIn("2026-11-02-ride", c.log_repo.all())
        c.strava.handle_event({"object_type": "activity", "aspect_type": "delete", "object_id": 1, "owner_id": 138526058})
        self.assertNotIn("2026-11-02-ride", c.log_repo.all())

    def test_to_activity_parses_local_time(self):
        a = to_activity(raw(5, "x", "Run", "2026-11-03", 10, 60))
        self.assertEqual(a.start_local.hour, 6)
        self.assertEqual(a.km, 10.0)
        self.assertAlmostEqual(a.pace_s_per_km, 360.0)

    def test_not_configured(self):
        c = make_container(date(2026, 11, 10), strava_client_id="", strava_client_secret="")
        with self.assertRaises(StravaError):
            c.strava.connect_url()


if __name__ == "__main__":
    unittest.main()

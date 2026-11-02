import base64
import hashlib
import json
import sqlite3
import unittest
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from app.integrations.google.client import AuthError, pkce_challenge
from app.integrations.strava.client import StravaError
from tests.helpers import make_app, make_settings
from app.config import Settings
from app.container import Container
from app.clock import FixedClock


def b64(d) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


class FakeGoogle:
    """Pretends to be Google's token endpoint; records what the app sent."""
    def __init__(self):
        self.claims, self.sent, self.status = {}, [], 200

    def request(self, method, url, params=None, data=None, headers=None):
        self.sent.append((method, url, data))
        if self.status != 200:
            return self.status, {"error": "invalid_grant"}, {}
        return 200, {"access_token": "x", "id_token": f"{b64({'alg': 'RS256'})}.{b64(self.claims)}.sig"}, {}


def claims(nonce, **over):
    base = {"iss": "https://accounts.google.com", "aud": "gid", "sub": "g-123", "email": "me@example.com",
            "email_verified": True, "name": "Me", "picture": "https://x/y.png", "exp": 4102444800, "nonce": nonce}
    base.update(over)
    return base


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.g = FakeGoogle()
        self.c = make_app(date(2026, 11, 10), google_http=self.g)

    def sign_in(self, **over):
        url = self.c.auth.login_url()
        q = parse_qs(urlparse(url).query)
        c = claims(q["nonce"][0])
        c.update(over)                      # lets a test override the nonce itself
        self.g.claims = c
        return self.c.auth.complete_login("CODE", q["state"][0]), q

    def test_login_url_uses_pkce_and_openid_scope(self):
        q = parse_qs(urlparse(self.c.auth.login_url()).query)
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertIn("openid", q["scope"][0])
        self.assertEqual(q["client_id"], ["gid"])

    def test_pkce_verifier_matches_challenge(self):
        (_, _), q = self.sign_in()
        sent = self.g.sent[-1][2]
        self.assertEqual(pkce_challenge(sent["code_verifier"]), q["code_challenge"][0])
        self.assertEqual(sent["client_secret"], "gsec")

    def test_success_creates_user_and_session(self):
        (user, token), _ = self.sign_in()
        self.assertEqual(user.email, "me@example.com")
        self.assertEqual(self.c.auth.user_for_token(token).id, user.id)
        self.assertIsNone(self.c.auth.user_for_token("garbage"))
        self.assertIsNone(self.c.auth.user_for_token(None))

    def test_only_a_hash_of_the_token_is_stored(self):
        (_, token), _ = self.sign_in()
        with self.c.db.connect() as db:
            rows = [r["token_hash"] for r in db.execute("SELECT token_hash FROM sessions").fetchall()]
        self.assertNotIn(token, rows)
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), rows)

    def test_logout_ends_the_session(self):
        (_, token), _ = self.sign_in()
        self.c.auth.logout(token)
        self.assertIsNone(self.c.auth.user_for_token(token))

    def test_session_expires(self):
        (_, token), _ = self.sign_in()
        self.c.clock._today = date(2026, 11, 10) + timedelta(days=self.c.settings.session_days + 1)
        self.assertIsNone(self.c.auth.user_for_token(token))

    def test_state_is_single_use_and_must_exist(self):
        url = self.c.auth.login_url()
        q = parse_qs(urlparse(url).query)
        self.g.claims = claims(q["nonce"][0])
        self.c.auth.complete_login("CODE", q["state"][0])
        with self.assertRaises(AuthError) as cm:
            self.c.auth.complete_login("CODE", q["state"][0])          # replay
        self.assertEqual(cm.exception.code, "bad_state")
        with self.assertRaises(AuthError):
            self.c.auth.complete_login("CODE", "forged")

    def test_bad_tokens_are_rejected(self):
        cases = {"invalid_token": [dict(aud="someone-else"), dict(iss="https://evil.example"), dict(exp=1),
                                   dict(nonce="wrong"), dict(sub="")],
                 "email_not_verified": [dict(email_verified=False)]}
        for code, variants in cases.items():
            for v in variants:
                with self.assertRaises(AuthError, msg=str(v)) as cm:
                    self.sign_in(**v)
                self.assertEqual(cm.exception.code, code, v)

    def test_allow_list(self):
        with self.assertRaises(AuthError) as cm:
            self.sign_in(email="stranger@example.com")
        self.assertEqual(cm.exception.code, "not_allowed")
        (user, _), _ = self.sign_in(email="ME@Example.com")            # case-insensitive
        self.assertEqual(user.email, "me@example.com")
        open_c = make_app(date(2026, 11, 10), google_http=self.g, allow_any_google_user=True)
        url = open_c.auth.login_url()
        q = parse_qs(urlparse(url).query)
        self.g.claims = claims(q["nonce"][0], email="anyone@example.com", sub="g-999")
        self.assertEqual(open_c.auth.complete_login("C", q["state"][0])[0].email, "anyone@example.com")

    def test_empty_allow_list_denies_everyone(self):
        c = make_app(date(2026, 11, 10), google_http=self.g, allowed_emails=())
        q = parse_qs(urlparse(c.auth.login_url()).query)
        self.g.claims = claims(q["nonce"][0])
        with self.assertRaises(AuthError):
            c.auth.complete_login("C", q["state"][0])

    def test_google_error_and_missing_config(self):
        self.g.status = 400
        with self.assertRaises(AuthError) as cm:
            self.sign_in()
        self.assertEqual(cm.exception.code, "token_exchange_failed")
        c = make_app(date(2026, 11, 10), google_client_id="", google_client_secret="")
        with self.assertRaises(AuthError) as cm:
            c.auth.login_url()
        self.assertEqual(cm.exception.code, "not_configured")


class IsolationTests(unittest.TestCase):
    def test_users_never_see_each_others_data(self):
        c = make_app(date(2026, 11, 10), allow_any_google_user=True)
        a = c.users.upsert_google("a", "a@example.com", "A", "", "2026-11-01T00:00:00")
        b = c.users.upsert_google("b", "b@example.com", "B", "", "2026-11-01T00:00:00")
        sa, sb = c.scope(a.id), c.scope(b.id)
        sa.checklist.set_status("2026-11-02-ride", "done", 30)
        sa.readiness.record(date(2026, 11, 3), 5, 8, 8, "low")
        va = next(x for x in sa.checklist.day_view(date(2026, 11, 2))["activities"] if x["type"] == "ride")
        vb = next(x for x in sb.checklist.day_view(date(2026, 11, 2))["activities"] if x["type"] == "ride")
        self.assertEqual((va["status"], vb["status"]), ("done", "missed"))
        self.assertIsNone(sb.readiness.get(date(2026, 11, 3)))
        self.assertEqual(sb.log_repo.all(), {})
        self.assertGreater(sa.analysis.analyse()["goals"][0]["actual"], 0)
        self.assertEqual(sb.analysis.analyse()["goals"][0]["actual"], 0)

    def test_strava_oauth_state_is_bound_to_the_user(self):
        from tests.test_strava import FakeHttp
        c = make_app(date(2026, 11, 10), http=FakeHttp(), allow_any_google_user=True)
        a = c.users.upsert_google("a", "a@example.com", "A", "", "2026-11-01T00:00:00")
        b = c.users.upsert_google("b", "b@example.com", "B", "", "2026-11-01T00:00:00")
        state = parse_qs(urlparse(c.scope(a.id).strava.connect_url()).query)["state"][0]
        with self.assertRaises(StravaError):
            c.scope(b.id).strava.handle_callback("CODE", state, "")      # B cannot finish A's connection
        # the state was burned by the failed attempt, so even A has to start again
        state = parse_qs(urlparse(c.scope(a.id).strava.connect_url()).query)["state"][0]
        c.scope(a.id).strava.handle_callback("CODE", state, "read")
        self.assertTrue(c.scope(a.id).strava.status()["connected"])
        self.assertFalse(c.scope(b.id).strava.status()["connected"])

    def test_webhook_finds_the_right_user(self):
        from tests.test_strava import FakeHttp
        c = make_app(date(2026, 11, 10), http=FakeHttp(), allow_any_google_user=True)
        a = c.users.upsert_google("a", "a@example.com", "A", "", "2026-11-01T00:00:00")
        state = parse_qs(urlparse(c.scope(a.id).strava.connect_url()).query)["state"][0]
        c.scope(a.id).strava.handle_callback("CODE", state, "")
        self.assertEqual(c.scope_for_athlete(138526058).user_id, a.id)
        self.assertIsNone(c.scope_for_athlete(1))


class LegacyMigrationTests(unittest.TestCase):
    def test_v1_database_is_upgraded_and_claimed_by_the_first_sign_in(self):
        s = make_settings()
        con = sqlite3.connect(s.db_path)           # a database as written by the pre-login version
        con.executescript("""
          CREATE TABLE checklist_log (activity_id TEXT PRIMARY KEY, status TEXT NOT NULL, actual_km REAL, actual_minutes INTEGER,
            notes TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual', strava_id INTEGER, updated_at TEXT NOT NULL);
          INSERT INTO checklist_log VALUES ('2026-11-02-ride','done',30,75,'old','manual',NULL,'2026-11-02T10:00:00');
          CREATE TABLE strava_tokens (id INTEGER PRIMARY KEY CHECK (id = 1), athlete_id INTEGER, access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL, expires_at INTEGER NOT NULL, scope TEXT, last_sync TEXT);
          INSERT INTO strava_tokens VALUES (1, 138526058, 'AT', 'RT', 4102444800, 'read', NULL);
        """)
        con.commit(); con.close()
        g = FakeGoogle()
        c = Container(s, clock=FixedClock(date(2026, 11, 10)), google_http=g)
        self.assertTrue(c.db.has_legacy())
        q = parse_qs(urlparse(c.auth.login_url()).query)
        g.claims = claims(q["nonce"][0])
        user, _ = c.auth.complete_login("CODE", q["state"][0])
        scope = c.scope(user.id)
        self.assertEqual(scope.log_repo.all()["2026-11-02-ride"].notes, "old")
        self.assertEqual(scope.strava_repo.get_tokens()["athlete_id"], 138526058)
        self.assertFalse(c.db.has_legacy())


if __name__ == "__main__":
    unittest.main()

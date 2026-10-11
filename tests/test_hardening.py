"""Launch hardening: rate limits, the daily-run lock, editing a child, email
verification, file storage, database migrations, error alerts and fonts."""

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import jobs, storage
from app.models import Child, JobLock, now
from tests.test_app import AppTestCase


class RateLimitTests(AppTestCase):
    def test_login_is_rate_limited(self):
        self.signup()
        self.client.cookies.clear()
        codes = []
        for _ in range(12):
            r = self.client.post("/login", data={"csrf": self.csrf("/login"), "email": "parent@example.com",
                                                 "password": "wrong"})
            codes.append(r.status_code)
        self.assertEqual(codes[:10], [400] * 10)
        self.assertEqual(codes[10:], [429, 429])  # 10 tries per email per 15 minutes
        r = self.client.post("/login", data={"csrf": self.csrf("/login"), "email": "parent@example.com",
                                             "password": "a long password"})
        self.assertEqual(r.status_code, 429)  # even the right password waits

    def test_signups_from_one_address_are_limited(self):
        codes = []
        for i in range(6):
            c = TestClient(self.app)
            token = re.search(r'name="csrf" value="([^"]+)"', c.get("/signup").text).group(1)
            codes.append(c.post("/signup", data={"csrf": token, "name": "x", "email": f"p{i}@example.com",
                                                 "password": "a long password", "agree": "true"},
                                follow_redirects=False).status_code)
        self.assertEqual(codes, [303] * 5 + [429])

    def test_limits_can_be_switched_off(self):
        self.settings.rate_limits = False
        self.signup()
        self.client.cookies.clear()
        for _ in range(12):
            r = self.client.post("/login", data={"csrf": self.csrf("/login"), "email": "parent@example.com", "password": "x"})
        self.assertEqual(r.status_code, 400)


class LockTests(AppTestCase):
    def test_overlapping_runs_are_skipped_and_stale_locks_taken_over(self):
        sub_id = self.signup()
        self.activate(sub_id)
        db = self.db
        db.add(JobLock(name=jobs.LOCK_NAME))
        db.commit()
        self.assertTrue(jobs.run_due(db, self.settings, self.services).skipped)
        self.assertEqual(self.services.written, [])
        db.get(JobLock, jobs.LOCK_NAME).acquired_at = now() - jobs.STALE_LOCK - jobs.timedelta(minutes=1)
        db.commit()
        summary = jobs.run_due(db, self.settings, self.services)
        self.assertFalse(summary.skipped)
        self.assertEqual(summary.letters_written, 1)
        self.assertIsNone(db.get(JobLock, jobs.LOCK_NAME))  # released afterwards
        db.close()


class EditChildTests(AppTestCase):
    def test_parent_can_edit_and_others_cannot(self):
        self.signup()
        page = self.client.get("/children/1/edit").text
        self.assertIn('value="1 Example Street"', page)
        self.assertIn("sensitive details", page)
        r = self.client.post("/children/1/edit", data={
            "csrf": self.csrf("/children/1/edit"), "first_name": "Maya", "age": "10", "interests": "space",
            "recipient_name": "The Smith Family", "line1": "5 New Road", "town": "Bath", "postcode": "ba1 1aa"},
            follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        db = self.db
        child = db.get(Child, 1)
        self.assertEqual((child.age, child.line1, child.postcode, child.parent_preview), (10, "5 New Road", "BA1 1AA", False))
        db.close()
        other = TestClient(self.app)
        token = re.search(r'name="csrf" value="([^"]+)"', other.get("/signup").text).group(1)
        other.post("/signup", data={"csrf": token, "name": "B", "email": "b@example.com",
                                    "password": "a long password", "agree": "true"})
        self.assertEqual(other.get("/children/1/edit").status_code, 404)


class VerificationTests(AppTestCase):
    def test_signup_sends_link_and_bad_links_fail(self):
        with mock.patch("app.web.send_email") as send:
            self.signup()
        self.assertIn("confirm your email", send.call_args.args[2].lower())
        self.assertIn("confirm your email", self.client.get("/account").text)
        self.assertEqual(self.client.get("/verify/not-a-real-token").status_code, 400)
        link = re.search(r"/verify/(\S+)", send.call_args.args[3]).group(1)
        self.client.get(f"/verify/{link}")
        self.assertNotIn("Send it again", self.client.get("/account").text)


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        return {"Body": mock.Mock(read=lambda: self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


class StorageTests(unittest.TestCase):
    def check(self, store):
        store.put("letters/1/letter-1.pdf", b"%PDF", "application/pdf")
        self.assertTrue(store.exists("letters/1/letter-1.pdf"))
        self.assertEqual(store.get("letters/1/letter-1.pdf"), b"%PDF")
        with store.local_path("letters/1/letter-1.pdf") as path:
            self.assertEqual(Path(path).read_bytes(), b"%PDF")
        store.delete("letters/1/letter-1.pdf")
        self.assertFalse(store.exists("letters/1/letter-1.pdf"))

    def test_local_and_s3(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = storage.LocalStorage(Path(tmp))
            self.check(local)
            with self.assertRaises(ValueError):
                local.put("../escape.txt", b"x")
        self.check(storage.S3Storage("bucket", FakeS3()))

    def test_whole_letter_flow_works_on_s3(self):
        case = AppTestCase("run")
        case.setUp()
        try:
            fake = FakeS3()
            case.settings._storage = storage.S3Storage("bucket", fake)
            case.app = __import__("app.web", fromlist=["create_app"]).create_app(case.settings, case.services)
            case.client = TestClient(case.app)
            sub_id = case.signup()
            case.activate(sub_id)
            db = case.db
            jobs.run_due(db, case.settings, case.services)
            db.close()
            self.assertIn("letters/1/letter-1.pdf", fake.objects)
            admin = case.make_admin()
            case.admin_post(admin, "/admin/letters/1/approve")
            self.assertEqual(len(case.services.sent), 1)
            self.assertEqual(case.client.get("/letters/1.pdf").content, b"%PDF-1.4 fake")
        finally:
            case.tearDown()


class MigrationTests(unittest.TestCase):
    def test_migrations_build_the_same_schema_as_the_models(self):
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, inspect

        from app.models import Base
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            url = f"sqlite:///{tmp}/m.db"
            cfg = Config(str(root / "alembic.ini"))
            cfg.set_main_option("script_location", str(root / "migrations"))
            with mock.patch.dict("os.environ", {"DATABASE_URL": url}):
                command.upgrade(cfg, "head")
            inspector = inspect(create_engine(url))
            for table in Base.metadata.tables.values():
                columns = {c["name"] for c in inspector.get_columns(table.name)}
                self.assertEqual(columns, {c.name for c in table.columns}, table.name)


class MiscTests(unittest.TestCase):
    def test_error_alerts_are_off_without_a_dsn_and_never_send_bodies(self):
        from app.web import init_error_alerts
        settings = mock.Mock(sentry_dsn="")
        with mock.patch("sentry_sdk.init") as init:
            init_error_alerts(settings)
            init.assert_not_called()
            settings.sentry_dsn = "https://key@example.ingest.sentry.io/1"
            init_error_alerts(settings)
        kwargs = init.call_args.kwargs
        self.assertEqual((kwargs["send_default_pii"], kwargs["max_request_body_size"], kwargs["include_local_variables"]),
                         (False, "never", False))

    def test_no_google_fonts_on_the_site(self):
        root = Path(__file__).resolve().parent.parent
        for page in list((root / "app/templates").glob("*.html")) + [root / "site/index.html"]:
            self.assertNotIn("fonts.googleapis.com", page.read_text(), page.name)


if __name__ == "__main__":
    unittest.main()


class GoogleSignInTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.settings.google_client_id = "client-123"
        self.settings.google_client_secret = "secret"
        self.app = __import__("app.web", fromlist=["create_app"]).create_app(self.settings, self.services)
        self.client = TestClient(self.app)

    def start(self):
        r = self.client.get("/auth/google?plan=story", follow_redirects=False)
        self.assertIn("accounts.google.com", r.headers["location"])
        self.assertIn("client_id=client-123", r.headers["location"])
        return re.search(r"state=([^&]+)", r.headers["location"]).group(1)

    def callback(self, state, profile):
        with mock.patch("app.google_auth.fetch_profile", return_value=profile):
            return self.client.get(f"/auth/google/callback?code=abc&state={state}", follow_redirects=False)

    def test_button_only_shows_when_configured(self):
        self.assertIn("Continue with Google", self.client.get("/login").text)
        plain = TestClient(__import__("app.web", fromlist=["create_app"]).create_app(Settings_copy(self.settings), self.services))
        self.assertNotIn("Continue with Google", plain.get("/login").text)
        self.assertEqual(plain.get("/auth/google", follow_redirects=False).status_code, 404)

    def test_new_parent_must_accept_terms_then_is_verified(self):
        profile = {"sub": "g-1", "email": "new@example.com", "email_verified": True, "name": "Asha"}
        r = self.callback(self.start(), profile)
        self.assertEqual(r.headers["location"], "/signup/google")
        page = self.client.get("/signup/google").text
        self.assertIn("new@example.com", page)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        self.assertEqual(self.client.post("/signup/google", data={"csrf": token, "name": "Asha"}).status_code, 400)
        r = self.client.post("/signup/google", data={"csrf": token, "name": "Asha", "agree": "true"},
                             follow_redirects=False)
        self.assertEqual(r.headers["location"], "/children/new?plan=story")
        db = self.db
        from app.models import User
        user = db.query(User).filter_by(email="new@example.com").one()
        self.assertEqual((user.google_sub, user.password_hash), ("g-1", ""))
        self.assertIsNotNone(user.email_verified_at)
        db.close()
        # Next time, Google signs them straight in.
        self.client.cookies.clear()
        r = self.callback(self.start(), profile)
        self.assertEqual(r.headers["location"], "/account")

    def test_links_existing_account_with_same_verified_email(self):
        self.signup()  # password account, email not yet confirmed
        self.client.cookies.clear()
        r = self.callback(self.start(), {"sub": "g-2", "email": "parent@example.com", "email_verified": True, "name": "P"})
        self.assertEqual(r.headers["location"], "/account")
        db = self.db
        from app.models import User
        user = db.query(User).filter_by(email="parent@example.com").one()
        self.assertEqual(user.google_sub, "g-2")
        self.assertIsNotNone(user.email_verified_at)
        db.close()

    def test_rejects_bad_state_and_unverified_google_email(self):
        self.start()
        r = self.callback("forged-state", {"sub": "g", "email": "x@example.com", "email_verified": True, "name": ""})
        self.assertEqual(r.headers["location"], "/login")
        r = self.callback(self.start(), {"sub": "g", "email": "x@example.com", "email_verified": False, "name": ""})
        self.assertEqual(r.headers["location"], "/signup")


def Settings_copy(settings):
    import copy
    plain = copy.copy(settings)
    plain.google_client_id = plain.google_client_secret = ""
    return plain


class RetentionTests(AppTestCase):
    def test_old_letters_and_replies_are_cleared_but_rotation_survives(self):
        from app.models import Letter, Reply
        sub_id = self.signup()
        self.activate(sub_id)
        db = self.db
        jobs.run_due(db, self.settings, self.services)
        db.add(Reply(child_id=1, figure="mary-anning", typed_text="Dear Mary", transcription_json="{}", status="used"))
        db.add(Reply(child_id=1, figure="mary-anning", typed_text="worrying", status="held"))
        db.commit()
        old = now() - jobs.timedelta(days=400)
        letter = db.get(Letter, 1)
        letter.status, letter.created_at = "sent", old
        for r in db.query(Reply).all():
            r.created_at = old
        db.commit()
        pdf_key = letter.pdf_path
        jobs.apply_retention(db, self.settings)
        letter = db.get(Letter, 1)
        self.assertIsNone(letter.letter_json)
        self.assertFalse(jobs.get_storage(self.settings).exists(pdf_key))
        self.assertEqual((letter.kind, letter.figure), ("intro", "mary-anning"))  # rotation still knows
        used, held = db.query(Reply).order_by(Reply.id).all()
        self.assertEqual((used.typed_text, used.transcription_json), ("", None))
        self.assertEqual(held.typed_text, "worrying")  # left for the safeguarding lead
        self.assertEqual(jobs.next_story_kind(jobs.current_figure(db.get(Child, 1))[1]), "story")
        db.close()

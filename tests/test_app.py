"""Web app tests: accounts, Stripe billing, the daily job and admin review.
The AI, PDF maker and print-and-post service are replaced with fakes."""

import hashlib
import hmac
import json
import re
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import billing, jobs
from app.config import FIGURE_ORDER, Settings
from app.models import Child, Letter, Reply, Subscription, User, now
from app.web import create_app
from letters import pipeline

WEBHOOK_SECRET = "whsec_test"


def fake_letter() -> pipeline.Letter:
    return pipeline.Letter(greeting="Dear Maya,", body=["Hello from the past."], signoff="Your friend,\nAlbert",
                           postscript="What do you love?", real_history=["A true fact."])


class FakeServices(jobs.Services):
    def __init__(self, reply_result=None):
        self.written, self.sent, self.read = [], [], []
        self.reply_result = reply_result
        super().__init__(write_letter=self._write, read_reply=self._read, to_pdf=self._pdf, send=self._send)

    def _write(self, child, figure, reply=None, history="", kind="story", parent_note=""):
        self.written.append({"figure": figure, "kind": "reply" if reply else kind, "child": child,
                             "parent_note": parent_note})
        return pipeline.Result(fake_letter(), "ready", [pipeline.FactCheck(passes=True, issues=[])])

    def _read(self, photo=None, text=None):
        self.read.append(photo or text)
        return self.reply_result or pipeline.ChildReply(
            legible=True, text=text or "photo text", questions=["Were you scared?"], shared_news=[],
            needs_human=False, needs_human_reason="")

    def _pdf(self, html, path):
        Path(path).write_bytes(b"%PDF-1.4 fake")
        return 2

    def _send(self, pdf_path, to, live=False):
        self.sent.append({"to": to, "live": live})
        return {"id": f"ltr_{len(self.sent)}", "proof": "https://example/proof.pdf", "cost": "0.88"}


def sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    """Build a Stripe-Signature header exactly as Stripe does."""
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with mock.patch.dict("os.environ", {"DATA_DIR": self.tmp.name}, clear=False):
            self.settings = Settings()
        self.settings.stripe_secret_key = "sk_test_123"
        self.settings.stripe_webhook_secret = WEBHOOK_SECRET
        self.settings.stripe_prices = {"story": "price_story", "penpal": "price_penpal"}
        self.services = FakeServices()
        self.app = create_app(self.settings, self.services)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.state.SessionLocal.kw["bind"].dispose()
        self.tmp.cleanup()

    @property
    def db(self):
        return self.app.state.SessionLocal()

    def csrf(self, path="/signup"):
        return re.search(r'name="csrf" value="([^"]+)"', self.client.get(path).text).group(1)

    def signup(self, email="parent@example.com", plan="penpal"):
        r = self.client.post("/signup", data={"csrf": self.csrf(), "name": "Priya", "email": email,
                                              "password": "a long password", "agree": "true", "plan": plan},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text[:300])
        r = self.client.post("/children/new", data={
            "csrf": self.csrf("/children/new"), "first_name": "Maya", "age": "9", "interests": "space, cats",
            "recipient_name": "Maya Smith", "line1": "1 Example Street", "town": "Bristol",
            "postcode": "bs1 4st", "plan": plan, "guardian": "true"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text[:300])
        return int(r.headers["location"].rsplit("/", 1)[1])

    def webhook(self, event: dict):
        payload = json.dumps(event).encode()
        return self.client.post("/stripe/webhook", content=payload, headers={"stripe-signature": sign(payload)})

    def activate(self, sub_id: int, stripe_id="sub_123", event_suffix="1"):
        """Simulate a completed checkout and first paid invoice (current Stripe API shape)."""
        self.webhook({"id": f"evt_cs_{event_suffix}", "object": "event", "type": "checkout.session.completed",
                      "data": {"object": {"object": "checkout.session", "mode": "subscription", "subscription": stripe_id,
                                          "client_reference_id": str(sub_id), "metadata": {"subscription_row_id": str(sub_id)}}}})
        self.webhook({"id": f"evt_inv_{event_suffix}", "object": "event", "type": "invoice.paid",
                      "data": {"object": {"object": "invoice", "billing_reason": "subscription_create",
                                          "parent": {"subscription_details": {"subscription": stripe_id,
                                                                              "metadata": {"subscription_row_id": str(sub_id)}}}}}})

    def make_admin(self):
        db = self.db
        user = db.query(User).filter_by(email="admin@example.com").one_or_none()
        if not user:
            from app.security import hash_password
            user = User(email="admin@example.com", name="Admin", password_hash=hash_password("admin password 1"), is_admin=True)
            db.add(user)
            db.commit()
        db.close()
        admin = TestClient(self.app)
        token = re.search(r'name="csrf" value="([^"]+)"', admin.get("/login").text).group(1)
        admin.post("/login", data={"csrf": token, "email": "admin@example.com", "password": "admin password 1"})
        return admin

    def admin_post(self, admin, path):
        token = re.search(r'name="csrf" value="([^"]+)"', admin.get("/admin").text).group(1)
        return admin.post(path, data={"csrf": token}, follow_redirects=False)


class PublicAndAuthTests(AppTestCase):
    def test_public_pages(self):
        for path in ["/", "/privacy", "/terms", "/signup", "/login", "/healthz"]:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertIn("Forem Innovations", self.client.get("/privacy").text)
        self.assertIn("Forem Innovations", self.client.get("/").text)

    def test_account_requires_login_and_admin_is_hidden(self):
        r = self.client.get("/account", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/login"))
        self.signup()
        self.assertEqual(self.client.get("/admin").status_code, 404)

    def test_posts_need_csrf(self):
        r = self.client.post("/signup", data={"name": "x", "email": "x@example.com", "password": "a long password", "agree": "true"})
        self.assertEqual(r.status_code, 400)

    def test_signup_validation(self):
        r = self.client.post("/signup", data={"csrf": self.csrf(), "name": "x", "email": "x@example.com", "password": "short"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("at least 10 characters", r.text)
        self.assertIn("privacy policy", r.text)

    def test_login_and_wrong_password(self):
        self.signup()
        self.client.cookies.clear()
        r = self.client.post("/login", data={"csrf": self.csrf("/login"), "email": "parent@example.com", "password": "nope"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/login", data={"csrf": self.csrf("/login"), "email": "PARENT@example.com",
                                             "password": "a long password"}, follow_redirects=False)
        self.assertEqual(r.headers["location"], "/account")

    def test_password_reset_link(self):
        self.signup()
        with mock.patch("app.web.send_email") as send:
            self.client.post("/forgot", data={"csrf": self.csrf("/forgot"), "email": "parent@example.com"})
        link = re.search(r"/reset/(\S+)", send.call_args.args[3]).group(1)
        r = self.client.post(f"/reset/{link}", data={"csrf": self.csrf(f"/reset/{link}"), "password": "a brand new password"},
                             follow_redirects=False)
        self.assertEqual(r.headers["location"], "/account")
        # The link only works once.
        r = self.client.post(f"/reset/{link}", data={"csrf": self.csrf(f"/reset/{link}"), "password": "another password!"})
        self.assertEqual(r.status_code, 400)


class BillingTests(AppTestCase):
    def test_child_signup_creates_pending_subscription_and_checkout_redirects_to_stripe(self):
        sub_id = self.signup()
        db = self.db
        sub = db.get(Subscription, sub_id)
        self.assertEqual((sub.status, sub.plan, sub.child.postcode), ("pending", "penpal", "BS1 4ST"))
        db.close()
        with mock.patch("stripe.Customer.create", return_value=mock.Mock(id="cus_1")) as cust, \
             mock.patch("stripe.checkout.Session.create", return_value=mock.Mock(url="https://checkout.stripe.com/x")) as cs:
            r = self.client.post(f"/checkout/{sub_id}", data={"csrf": self.csrf("/account")}, follow_redirects=False)
        self.assertEqual(r.headers["location"], "https://checkout.stripe.com/x")
        params = cs.call_args.kwargs
        self.assertEqual(params["mode"], "subscription")
        self.assertEqual(params["line_items"], [{"price": "price_penpal", "quantity": 1}])
        self.assertEqual(params["subscription_data"]["metadata"]["subscription_row_id"], str(sub_id))
        cust.assert_called_once()

    def test_webhook_rejects_bad_signature(self):
        payload = json.dumps({"id": "evt_x", "type": "invoice.paid", "data": {"object": {}}}).encode()
        r = self.client.post("/stripe/webhook", content=payload, headers={"stripe-signature": sign(payload, "whsec_wrong")})
        self.assertEqual(r.status_code, 400)

    def test_paid_invoice_activates_and_grants_credits_once(self):
        sub_id = self.signup()
        self.activate(sub_id)
        self.activate(sub_id)  # same event ids again: Stripe retries must not double-credit
        db = self.db
        sub = db.get(Subscription, sub_id)
        self.assertEqual((sub.status, sub.stripe_subscription_id, sub.story_credits, sub.reply_credits),
                         ("active", "sub_123", 1, 1))
        db.close()

    def test_older_invoice_shape_and_cancellation(self):
        sub_id = self.signup(plan="story")
        db = self.db
        db.get(Subscription, sub_id).stripe_subscription_id = "sub_old"
        db.commit()
        db.close()
        self.webhook({"id": "evt_a", "object": "event", "type": "invoice.paid",
                      "data": {"object": {"billing_reason": "subscription_cycle", "subscription": "sub_old"}}})
        self.webhook({"id": "evt_b", "object": "event", "type": "customer.subscription.deleted",
                      "data": {"object": {"id": "sub_old", "status": "canceled", "metadata": {}}}})
        db = self.db
        sub = db.get(Subscription, sub_id)
        self.assertEqual((sub.story_credits, sub.reply_credits, sub.status), (1, 0, "canceled"))
        db.close()


class LetterFlowTests(AppTestCase):
    def run_job(self):
        db = self.db
        summary = jobs.run_due(db, self.settings, self.services)
        db.close()
        return summary

    def grant(self, sub_id, story=1, reply=0):
        db = self.db
        sub = db.get(Subscription, sub_id)
        sub.story_credits += story
        sub.reply_credits += reply
        db.commit()
        db.close()

    def test_first_letter_is_an_intro_then_story_then_farewell_then_new_pen_pal(self):
        sub_id = self.signup(plan="story")
        self.activate(sub_id)
        admin = self.make_admin()
        kinds = []
        for month in range(4):
            if month:
                self.grant(sub_id)
            self.run_job()
            db = self.db
            letter = db.query(Letter).order_by(Letter.id.desc()).first()
            self.assertEqual(letter.status, "needs_review")  # a person approves every letter by default
            db.close()
            r = self.admin_post(admin, f"/admin/letters/{letter.id}/approve")
            self.assertEqual(r.status_code, 303)
            kinds.append((self.services.written[-1]["figure"], self.services.written[-1]["kind"]))
        self.assertEqual(kinds, [(FIGURE_ORDER[0], "intro"), (FIGURE_ORDER[0], "story"),
                                 (FIGURE_ORDER[0], "farewell"), (FIGURE_ORDER[1], "intro")])
        self.assertEqual(len(self.services.sent), 4)
        self.assertFalse(self.services.sent[0]["live"])  # test proofs unless LIVE_MAIL
        self.assertEqual(self.services.sent[0]["to"]["postcode"], "BS1 4ST")
        # The AI only ever sees the first name, never the surname or address.
        child = self.services.written[0]["child"]
        self.assertEqual(child["name"], "Maya")
        self.assertNotIn("Smith", json.dumps(child))
        self.assertNotIn("Example Street", json.dumps(child))

    def test_reply_photo_is_read_deleted_and_answered(self):
        sub_id = self.signup()
        self.activate(sub_id)
        self.run_job()  # intro
        token = self.csrf("/account")
        r = self.client.post("/children/1/reply", data={"csrf": token, "figure": "einstein"},
                             files={"photo": ("letter.jpg", b"\xff\xd8fakejpeg", "image/jpeg")}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        uploads = list(self.settings.uploads_dir.iterdir())
        self.assertEqual(len(uploads), 1)
        self.run_job()
        self.assertEqual(list(self.settings.uploads_dir.iterdir()), [])  # photo deleted after reading
        self.assertEqual(self.services.written[-1]["kind"], "reply")
        db = self.db
        reply = db.query(Reply).one()
        self.assertEqual((reply.status, reply.photo_path), ("used", None))
        self.assertEqual(db.get(Subscription, sub_id).reply_credits, 0)
        db.close()

    def test_worrying_reply_is_held_for_a_person(self):
        self.services.reply_result = pipeline.ChildReply(
            legible=True, text="...", questions=[], shared_news=[], needs_human=True, needs_human_reason="mentions being hurt")
        sub_id = self.signup()
        self.activate(sub_id)
        self.run_job()
        self.client.post("/children/1/reply", data={"csrf": self.csrf("/account"), "text": "a worrying letter"})
        self.run_job()
        self.assertNotIn("reply", [w["kind"] for w in self.services.written])
        admin = self.make_admin()
        self.assertIn("mentions being hurt", admin.get("/admin").text)
        self.assertIn("0808 800 5000", admin.get("/admin").text)  # safeguarding steps shown to the reviewer
        self.admin_post(admin, "/admin/replies/1/release")
        self.services.reply_result = None
        self.run_job()
        self.assertEqual(self.services.written[-1]["kind"], "reply")

    def test_rule_screen_holds_a_reply_the_ai_missed_keeps_photo_and_alerts_urgently(self):
        # The (fake) AI says this letter is fine; the rule-based screen must still hold it.
        self.services.reply_result = pipeline.ChildReply(
            legible=True, text="my dad hits me", questions=[], shared_news=[], needs_human=False,
            needs_human_reason="", risk_level="urgent")
        self.settings.admin_email = "safeguarding@example.com"
        sub_id = self.signup()
        self.activate(sub_id)
        self.run_job()
        self.client.post("/children/1/reply", data={"csrf": self.csrf("/account")},
                         files={"photo": ("l.jpg", b"\xff\xd8jpeg", "image/jpeg")})
        with mock.patch("app.jobs.send_email") as send:
            self.run_job()
        self.assertTrue(send.call_args.args[2].startswith("URGENT"))
        self.assertIn("999", send.call_args.args[3])
        self.assertEqual(len(list(self.settings.uploads_dir.iterdir())), 1)  # kept for the reviewer
        admin = self.make_admin()
        self.assertEqual(admin.get("/admin/replies/1/photo").status_code, 200)
        self.assertEqual(self.client.get("/admin/replies/1/photo").status_code, 404)  # parents can't
        self.admin_post(admin, "/admin/replies/1/dismiss")
        self.assertEqual(list(self.settings.uploads_dir.iterdir()), [])  # deleted after review

    def test_no_reply_in_time_becomes_follow_up_and_reject_returns_credit(self):
        sub_id = self.signup()
        self.activate(sub_id)
        self.run_job()
        self.assertEqual(len(self.services.written), 1)  # reply credit waits for a reply
        db = self.db
        db.get(Subscription, sub_id).reply_credit_at = now() - timedelta(days=22)
        db.commit()
        db.close()
        self.run_job()
        self.assertEqual(self.services.written[-1]["kind"], "follow_up")
        admin = self.make_admin()
        self.admin_post(admin, "/admin/letters/2/reject")
        db = self.db
        self.assertEqual(db.get(Letter, 2).status, "rejected")
        self.assertEqual(db.get(Subscription, sub_id).reply_credits, 1)
        db.close()

    def test_parents_see_only_their_own_posted_letters(self):
        sub_id = self.signup()
        self.activate(sub_id)
        self.run_job()
        self.assertEqual(self.client.get("/letters/1.pdf").status_code, 404)  # not posted yet
        admin = self.make_admin()
        self.admin_post(admin, "/admin/letters/1/approve")
        self.assertEqual(self.client.get("/letters/1.pdf").status_code, 200)
        other = TestClient(self.app)
        token = re.search(r'name="csrf" value="([^"]+)"', other.get("/signup").text).group(1)
        other.post("/signup", data={"csrf": token, "name": "B", "email": "b@example.com",
                                    "password": "a long password", "agree": "true"})
        self.assertEqual(other.get("/letters/1.pdf").status_code, 404)


class ParentPreviewTests(AppTestCase):
    def setUp(self):
        super().setUp()
        sub_id = self.signup()
        db = self.db
        child = db.query(Child).one()
        child.parent_preview = True
        db.commit()
        db.close()
        self.activate(sub_id)
        self.admin = self.make_admin()
        db = self.db
        jobs.run_due(db, self.settings, self.services)
        db.close()
        self.admin_post(self.admin, "/admin/letters/1/approve")  # our check comes first

    def letter(self, letter_id=1):
        db = self.db
        letter = db.get(Letter, letter_id)
        db.close()
        return letter

    def test_letter_waits_for_the_parent_after_our_approval(self):
        self.assertEqual(self.letter().status, "awaiting_parent")
        self.assertEqual(self.services.sent, [])
        page = self.client.get("/letters/1/preview")
        self.assertIn("has written to Maya", page.text)
        self.assertEqual(self.client.get("/letters/1.html").status_code, 200)
        self.assertEqual(self.client.get("/letters/1.pdf").status_code, 200)

    def test_parent_approves_and_it_posts(self):
        token = re.search(r'name="csrf" value="([^"]+)"', self.client.get("/letters/1/preview").text).group(1)
        self.client.post("/letters/1/approve", data={"csrf": token})
        self.assertEqual(self.letter().status, "sent")
        self.assertEqual(len(self.services.sent), 1)

    def test_posts_automatically_when_the_window_ends(self):
        db = self.db
        db.get(Letter, 1).preview_deadline = now() - timedelta(minutes=1)
        db.commit()
        jobs.run_due(db, self.settings, self.services)
        db.close()
        self.assertEqual(self.letter().status, "sent")
        self.assertIn("automatically", self.letter().note)

    def test_changes_are_rewritten_with_the_note_and_capped(self):
        from app.config import PARENT_REWRITES_PER_MONTH
        for n in range(PARENT_REWRITES_PER_MONTH):
            letter_id = n + 1
            token = re.search(r'name="csrf" value="([^"]+)"', self.client.get(f"/letters/{letter_id}/preview").text).group(1)
            self.client.post(f"/letters/{letter_id}/changes", data={"csrf": token, "note": f"shorter please {n}"})
            self.assertEqual(self.letter(letter_id).status, "parent_rewrite")
            db = self.db
            self.assertEqual(db.get(Subscription, 1).story_credits, 1)  # the credit comes back, never more
            jobs.run_due(db, self.settings, self.services)
            db.close()
            self.assertEqual(self.services.written[-1]["parent_note"], f"shorter please {n}")
            self.admin_post(self.admin, f"/admin/letters/{letter_id + 1}/approve")  # rewrites are checked again
        last = PARENT_REWRITES_PER_MONTH + 1
        page = self.client.get(f"/letters/{last}/preview").text
        self.assertIn("used this month's rewrites", page)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        self.client.post(f"/letters/{last}/changes", data={"csrf": token, "note": "again"})
        self.assertEqual(self.letter(last).status, "awaiting_parent")  # refused
        self.assertEqual(len(self.services.written), PARENT_REWRITES_PER_MONTH + 1)

    def test_other_parents_cannot_preview_or_approve(self):
        other = TestClient(self.app)
        token = re.search(r'name="csrf" value="([^"]+)"', other.get("/signup").text).group(1)
        other.post("/signup", data={"csrf": token, "name": "B", "email": "b@example.com",
                                    "password": "a long password", "agree": "true"})
        self.assertEqual(other.get("/letters/1/preview").status_code, 404)
        self.assertEqual(other.get("/letters/1.html").status_code, 404)
        token = re.search(r'name="csrf" value="([^"]+)"', other.get("/account").text).group(1)
        self.assertEqual(other.post("/letters/1/approve", data={"csrf": token}).status_code, 404)
        self.assertEqual(self.letter().status, "awaiting_parent")

    def test_reply_uploads_are_capped(self):
        from app.config import REPLY_UPLOADS_PER_MONTH
        for i in range(REPLY_UPLOADS_PER_MONTH + 1):
            self.client.post("/children/1/reply", data={"csrf": self.csrf("/account"), "text": f"letter {i}"})
        db = self.db
        self.assertEqual(db.query(Reply).count(), REPLY_UPLOADS_PER_MONTH)
        db.close()


class CronEndpointTests(AppTestCase):
    def test_run_due_needs_the_secret(self):
        self.assertEqual(self.client.post("/internal/run-due").status_code, 404)  # no secret configured
        self.settings.cron_secret = "s3cret"
        self.assertEqual(self.client.post("/internal/run-due", headers={"Authorization": "Bearer nope"}).status_code, 404)
        sub_id = self.signup()
        self.activate(sub_id)
        r = self.client.post("/internal/run-due", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(r.json()["letters_written"], 1)


class PrivacyTests(AppTestCase):
    def test_export_and_delete(self):
        sub_id = self.signup()
        self.activate(sub_id)
        self.client.post("/children/1/reply", data={"csrf": self.csrf("/account"), "text": "Dear Albert"})
        data = self.client.get("/account/export").json()
        self.assertEqual(data["children"][0]["first_name"], "Maya")
        self.assertEqual(data["children"][0]["replies"][0]["typed_text"], "Dear Albert")

        with mock.patch.object(billing, "cancel_all") as cancel:
            r = self.client.post("/account/delete", data={"csrf": self.csrf("/account"), "confirm": "DELETE"},
                                 follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        cancel.assert_called_once()  # live Stripe subscription is cancelled
        db = self.db
        self.assertEqual((db.query(User).count(), db.query(Child).count(), db.query(Reply).count()), (0, 0, 0))
        db.close()


if __name__ == "__main__":
    unittest.main()

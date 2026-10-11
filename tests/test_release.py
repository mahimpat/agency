"""v1.0 release features: marketing preferences and unsubscribe, plan switching,
pausing a child, safeguarding records, resending lost letters, security headers,
and the connection check."""

import re
from unittest import mock

from fastapi.testclient import TestClient

from app import jobs
from app.emails import send_marketing_email
from app.models import Child, Letter, Reply, SafeguardingRecord, Subscription, User
from letters import pipeline
from tests.test_app import AppTestCase


class MarketingTests(AppTestCase):
    def test_preference_in_account_and_unsubscribe_link(self):
        self.signup()
        self.client.post("/account/preferences", data={"csrf": self.csrf("/account"), "marketing": "true"})
        db = self.db
        user = db.query(User).one()
        self.assertTrue(user.marketing_opt_in)
        with mock.patch("app.emails.send_email") as send:
            self.assertTrue(send_marketing_email(self.settings, user, "New pen pals!", "Meet Grace Darling."))
        db.close()
        link = re.search(r"/unsubscribe/(\S+)", send.call_args.args[3]).group(1)
        anonymous = TestClient(self.app)
        self.assertIn("Stop news and offers?", anonymous.get(f"/unsubscribe/{link}").text)  # a GET changes nothing
        self.assertTrue(self.db.query(User).one().marketing_opt_in)
        anonymous.post(f"/unsubscribe/{link}")
        db = self.db
        user = db.query(User).one()
        self.assertFalse(user.marketing_opt_in)
        with mock.patch("app.emails.send_email") as send:
            self.assertFalse(send_marketing_email(self.settings, user, "x", "y"))
            send.assert_not_called()
        db.close()
        self.assertEqual(anonymous.get("/unsubscribe/forged").status_code, 400)


class PlanSwitchTests(AppTestCase):
    def test_switching_plan_in_the_portal_updates_our_records(self):
        sub_id = self.signup()
        self.activate(sub_id)
        self.webhook({"id": "evt_switch", "object": "event", "type": "customer.subscription.updated",
                      "data": {"object": {"id": "sub_123", "status": "active", "metadata": {},
                                          "items": {"data": [{"price": {"id": "price_story"}}]}}}})
        db = self.db
        sub = db.get(Subscription, sub_id)
        self.assertEqual((sub.plan, sub.reply_credits), ("story", 0))
        db.close()


class PauseAndRecordTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.sub_id = self.signup()
        self.activate(self.sub_id)
        self.admin = self.make_admin()

    def run_job(self):
        db = self.db
        summary = jobs.run_due(db, self.settings, self.services)
        db.close()
        return summary

    def test_held_reply_needs_notes_can_pause_and_records_survive_account_deletion(self):
        self.services.reply_result = pipeline.ChildReply(
            legible=True, text="...", questions=[], shared_news=[], needs_human=True, needs_human_reason="worry")
        self.run_job()
        self.client.post("/children/1/reply", data={"csrf": self.csrf("/account"), "text": "a worrying letter"})
        self.run_job()
        self.admin_post(self.admin, "/admin/replies/1/dismiss")  # no notes: refused
        db = self.db
        self.assertEqual(db.get(Reply, 1).status, "held")
        db.close()
        self.admin_post(self.admin, "/admin/replies/1/dismiss", notes="Rang the council, case open.", pause="true")
        db = self.db
        child = db.get(Child, 1)
        self.assertIsNotNone(child.paused_at)
        actions = [r.action for r in db.query(SafeguardingRecord).order_by(SafeguardingRecord.id)]
        self.assertEqual(actions, ["dismissed", "paused"])
        self.assertEqual(db.query(SafeguardingRecord).first().author, "admin@example.com")
        db.get(Subscription, self.sub_id).story_credits = 1
        db.commit()
        db.close()
        written = len(self.services.written)
        self.run_job()
        self.assertEqual(len(self.services.written), written)  # paused: nothing written
        self.assertIn("Rang the council", self.admin.get("/admin/safeguarding").text)
        self.assertEqual(self.client.get("/admin/safeguarding").status_code, 404)  # parents can't see it
        # Records survive the parent deleting their account.
        with mock.patch("app.billing.cancel_all"):
            self.client.post("/account/delete", data={"csrf": self.csrf("/account"), "confirm": "DELETE"})
        db = self.db
        self.assertEqual(db.query(Child).count(), 0)
        self.assertEqual(db.query(SafeguardingRecord).count(), 2)
        self.assertIn("deleted their account", db.query(SafeguardingRecord).first().notes)
        db.close()

    def test_paused_child_gets_no_posts_until_resumed(self):
        self.run_job()
        self.admin_post(self.admin, "/admin/children/1/pause", reason="Checking a concern")
        self.admin_post(self.admin, "/admin/letters/1/approve")
        self.assertEqual(self.services.sent, [])
        self.assertIn("paused", self.admin.get("/admin").text)
        self.admin_post(self.admin, "/admin/children/1/pause", reason="Concern resolved", resume="true")
        self.admin_post(self.admin, "/admin/letters/1/approve")
        self.assertEqual(len(self.services.sent), 1)

    def test_resend_a_lost_letter(self):
        self.run_job()
        self.admin_post(self.admin, "/admin/letters/1/approve")
        self.admin_post(self.admin, "/admin/letters/1/resend")
        db = self.db
        letter = db.get(Letter, 1)
        self.assertEqual((letter.status, letter.resent_count), ("sent", 1))
        db.close()
        self.assertEqual(len(self.services.sent), 2)
        self.assertEqual(self.services.sent[1]["to"]["postcode"], "BS1 4ST")


class HeaderTests(AppTestCase):
    def test_security_headers(self):
        r = self.client.get("/")
        self.assertIn("frame-ancestors 'self'", r.headers["content-security-policy"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["x-frame-options"], "SAMEORIGIN")
        self.assertNotIn("strict-transport-security", r.headers)  # only over https
        self.settings.base_url = "https://lettersfromhistory.co.uk"
        self.assertIn("max-age", self.client.get("/").headers["strict-transport-security"])


class CheckCommandTests(AppTestCase):
    def test_check_reports_each_service(self):
        from app import check
        results = check.run_checks(self.settings, stannp_proof=False)
        names = {r.name for r in results}
        for expected in ("Settings", "Database", "File storage", "PDF maker", "Claude API", "Stripe", "Stannp", "Email"):
            self.assertIn(expected, names)
        self.assertTrue(next(r for r in results if r.name == "Database").ok)
        self.assertTrue(next(r for r in results if r.name == "File storage").ok)

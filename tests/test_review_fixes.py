"""Regression tests for issues found in the code review."""

import re
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app import gifts, jobs, referrals
from app.models import Gift, Letter, Referral, Subscription, User, now
from app.seasonal import christmas_notice
from tests.test_app import AppTestCase


class ScheduledGiftEmailTests(AppTestCase):
    def paid_gift(self, send_on):
        db = self.db
        gift = gifts.create_gift(db, 3, "Granny", "granny@example.com", "Maya", "", recipient_email="mum@example.com",
                                 send_on=send_on)
        gift.status, gift.expires_at = "paid", now() + timedelta(days=300)
        db.commit()
        gift_id = gift.id
        db.close()
        return gift_id

    def gift(self, gift_id):
        db = self.db
        g = db.get(Gift, gift_id)
        db.expunge(g)
        db.close()
        return g

    def test_failed_send_keeps_address_and_retries(self):
        gift_id = self.paid_gift(date.today())
        with mock.patch("app.gifts.send_email", return_value=False):
            self.assertEqual(gifts.send_due_to_recipients(self.settings, self.db), 0)
        g = self.gift(gift_id)
        self.assertEqual((g.recipient_email, g.sent_to_recipient_at), ("mum@example.com", None))
        with mock.patch("app.gifts.send_email", return_value=True) as send:
            self.assertEqual(gifts.send_due_to_recipients(self.settings, self.db), 1)
        self.assertEqual(send.call_args.args[1], "mum@example.com")
        self.assertEqual(self.gift(gift_id).recipient_email, "")

    def test_claimed_gift_is_not_sent_twice(self):
        gift_id = self.paid_gift(date.today())
        db1, db2 = self.db, self.db
        g1, g2 = db1.get(Gift, gift_id), db2.get(Gift, gift_id)  # two processes, same moment
        with mock.patch("app.gifts.send_email", return_value=True) as send:
            gifts.send_to_recipient(self.settings, db1, g1)
            gifts.send_to_recipient(self.settings, db2, g2)
        self.assertEqual(send.call_count, 1)
        db1.close()
        db2.close()

    def test_address_dropped_when_gift_redeemed_early(self):
        gift_id = self.paid_gift(date.today() + timedelta(days=30))
        db = self.db
        db.get(Gift, gift_id).status = "redeemed"
        db.commit()
        db.close()
        gifts.run_gift_months(self.db, self.settings)
        self.assertEqual(self.gift(gift_id).recipient_email, "")


class ReferralDiscountScopeTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.activate(self.signup(email="inviter@example.com"), stripe_id="sub_inv", event_suffix="inv")
        self.code = re.search(r'/r/([A-Z0-9]+)', self.client.get("/account").text).group(1)
        self.friend = TestClient(self.app)
        self.friend.get(f"/r/{self.code}")
        token = re.search(r'name="csrf" value="([^"]+)"', self.friend.get("/signup").text).group(1)
        self.friend.post("/signup", data={"csrf": token, "name": "F", "email": "f@example.com",
                                          "password": "a long password", "agree": "true"})
        self.subs = [self.add_child(n) for n in ("Ava", "Ben")]

    def add_child(self, name):
        token = re.search(r'name="csrf" value="([^"]+)"', self.friend.get("/children/new").text).group(1)
        r = self.friend.post("/children/new", data={"csrf": token, "first_name": name, "age": "8", "recipient_name": name,
                                                    "line1": "9 Other Road", "town": "Leeds", "postcode": "LS1 1AA",
                                                    "plan": "penpal", "guardian": "true"}, follow_redirects=False)
        return int(r.headers["location"].rsplit("/", 1)[1])

    def applies(self, sub_id):
        db = self.db
        user = db.query(User).filter_by(email="f@example.com").one()
        result = referrals.discount_applies(db, user, db.get(Subscription, sub_id))
        db.close()
        return result

    def test_only_first_child_gets_the_discount(self):
        self.assertTrue(self.applies(self.subs[0]))
        self.assertFalse(self.applies(self.subs[1]))
        self.assertIn("Your invite code gives you", self.friend.get(f"/checkout/{self.subs[0]}").text)
        self.assertNotIn("Your invite code gives you", self.friend.get(f"/checkout/{self.subs[1]}").text)

    def test_no_discount_when_billing_waits_for_a_gift_to_end(self):
        db = self.db
        sub = db.get(Subscription, self.subs[0])
        sub.gift_id, sub.status, sub.gift_months_left = 99, "active", 0
        sub.next_credit_at = now() + timedelta(days=20)
        db.commit()
        db.close()
        self.assertFalse(self.applies(self.subs[0]))


class HundredPercentCodeOnGiftTests(AppTestCase):
    def test_free_month_from_a_code_still_counts_as_paid(self):
        sub_id = self.signup()
        db = self.db
        sub = db.get(Subscription, sub_id)
        sub.gift_id, sub.status = 7, "gift_ended"
        db.commit()
        db.close()
        self.webhook({"id": "evt_free_code", "object": "event", "type": "invoice.paid",
                      "data": {"object": {"object": "invoice", "billing_reason": "subscription_create",
                                          "amount_paid": 0, "subtotal": 1200,
                                          "parent": {"subscription_details": {"subscription": "sub_x",
                                                     "metadata": {"subscription_row_id": str(sub_id)}}}}}})
        db = self.db
        sub = db.get(Subscription, sub_id)
        self.assertEqual(sub.story_credits, 1)
        self.assertIsNotNone(sub.started_at)
        db.close()


class FlyerFailureTests(AppTestCase):
    def test_letter_posts_without_flyer_if_it_fails(self):
        self.activate(self.signup())
        with mock.patch("app.jobs.flyer_pdf", side_effect=RuntimeError("chrome crashed")):
            jobs.run_due(self.db, self.settings, self.services)
        db = self.db
        letter = db.get(Letter, 1)
        self.assertIsNotNone(letter.pdf_path)
        self.assertEqual(letter.status, "needs_review")
        self.assertIn("flyer couldn't be made", letter.note)
        self.assertNotIn("PDF not made", letter.note)
        db.close()


class ChristmasMonthTests(AppTestCase):
    def test_last_order_day_in_november(self):
        with mock.patch.dict("os.environ", {"CHRISTMAS_LAST_ORDER": "11-30"}):
            self.assertIn("Monday 30 November", christmas_notice(date(2026, 11, 2))["text"])

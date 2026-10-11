"""Refer a friend: invite links, the friend's discount, the referrer's free month, and the guards."""

import re
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import stripe
from fastapi.testclient import TestClient

from app import referrals
from app.models import Referral, Subscription, User, now
from tests.test_app import AppTestCase


class ReferralTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.referrer_sub = self.signup(email="inviter@example.com")
        self.activate(self.referrer_sub, stripe_id="sub_inviter", event_suffix="inviter")
        page = self.client.get("/account").text
        self.invite = re.search(r'id="invite-link" value="([^"]+)"', page).group(1)
        self.code = self.invite.rsplit("/", 1)[1]
        self.friend = TestClient(self.app)

    def friend_signup(self, email="friend@example.com", postcode="M1 1AA", line1="9 Other Road"):
        c = self.friend
        c.get(f"/r/{self.code}")
        token = re.search(r'name="csrf" value="([^"]+)"', c.get("/signup").text).group(1)
        c.post("/signup", data={"csrf": token, "name": "Sam", "email": email, "password": "a long password",
                                "agree": "true", "plan": "penpal"})
        token = re.search(r'name="csrf" value="([^"]+)"', c.get("/children/new").text).group(1)
        r = c.post("/children/new", data={"csrf": token, "first_name": "Ava", "age": "7", "recipient_name": "Ava",
                                          "line1": line1, "town": "Manchester", "postcode": postcode,
                                          "plan": "penpal", "guardian": "true"}, follow_redirects=False)
        return int(r.headers["location"].rsplit("/", 1)[1])

    def friend_pays(self, sub_id):
        self.activate(sub_id, stripe_id="sub_friend", event_suffix="friend")

    def referral(self):
        db = self.db
        r = db.query(Referral).one()
        db.expunge(r)
        db.close()
        return r

    def age_referral(self, days=15):
        db = self.db
        db.query(Referral).one().paid_at = now() - timedelta(days=days)
        db.commit()
        db.close()

    def reward(self):
        with mock.patch("stripe.Customer.create_balance_transaction") as credit, \
                mock.patch("app.billing.ensure_customer", return_value="cus_inviter"), \
                mock.patch("app.referrals.send_email") as send:
            count = referrals.run_rewards(self.db, self.settings)
        return count, credit, send

    def test_invite_link_banner_and_signup(self):
        self.assertTrue(self.code.isalnum())
        landing = self.friend.get(f"/r/{self.code}").text
        self.assertIn("A friend invited you", landing)
        self.friend_signup()
        ref = self.referral()
        db = self.db
        friend = db.query(User).filter_by(email="friend@example.com").one()
        inviter = db.query(User).filter_by(email="inviter@example.com").one()
        self.assertEqual((ref.referrer_id, ref.friend_id, ref.status), (inviter.id, friend.id, "signed_up"))
        self.assertEqual((friend.referred_by_id, friend.source), (inviter.id, "referral"))
        db.close()

    def test_no_invite_before_letters_start(self):
        other = TestClient(self.app)
        token = re.search(r'name="csrf" value="([^"]+)"', other.get("/signup").text).group(1)
        other.post("/signup", data={"csrf": token, "name": "N", "email": "new@example.com",
                                    "password": "a long password", "agree": "true"})
        self.assertNotIn("invite-link", other.get("/account").text)

    def test_bad_code_and_own_link_do_nothing(self):
        self.client.get(f"/r/{self.code}")  # the inviter, logged in
        self.friend.get("/r/NOPE99")
        self.assertNotIn("A friend invited you", self.friend.get("/").text)

    def test_friend_gets_discount_at_checkout_only_once(self):
        sub_id = self.friend_signup()
        self.assertIn("50% off your first month", self.friend.get(f"/checkout/{sub_id}").text)
        db = self.db
        db.query(User).filter_by(email="friend@example.com").one().email_verified_at = now()
        db.commit()
        db.close()
        token = re.search(r'name="csrf" value="([^"]+)"', self.friend.get(f"/checkout/{sub_id}").text).group(1)
        missing = stripe.InvalidRequestError("No such coupon", "id")
        with mock.patch("app.billing.ensure_customer", return_value="cus_friend"), \
                mock.patch("stripe.Coupon.retrieve", side_effect=missing), \
                mock.patch("stripe.Coupon.create") as coupon, \
                mock.patch("stripe.checkout.Session.create", return_value=SimpleNamespace(url="https://pay")) as create:
            self.friend.post(f"/checkout/{sub_id}", data={"csrf": token})
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["discounts"], [{"coupon": "referral-50-once"}])
        self.assertNotIn("allow_promotion_codes", kwargs)
        self.assertEqual(coupon.call_args.kwargs["duration"], "once")

        self.friend_pays(sub_id)
        self.assertEqual(self.referral().status, "paid")
        db = self.db
        friend = db.query(User).filter_by(email="friend@example.com").one()
        self.assertFalse(referrals.discount_applies(db, friend))
        db.close()

    def test_ordinary_checkout_allows_promotion_codes(self):
        db = self.db
        sub = db.get(Subscription, self.referrer_sub)
        sub.status = "canceled"
        db.commit()
        user = sub.user
        with mock.patch("app.billing.ensure_customer", return_value="cus_inviter"), \
                mock.patch("stripe.checkout.Session.create", return_value=SimpleNamespace(url="https://pay")) as create:
            from app import billing
            billing.checkout_url(self.settings, db, user, sub)
        db.close()
        self.assertTrue(create.call_args.kwargs["allow_promotion_codes"])
        self.assertNotIn("discounts", create.call_args.kwargs)

    def test_referrer_gets_free_month_after_hold(self):
        self.friend_pays(self.friend_signup())
        count, credit, _ = self.reward()
        self.assertEqual(count, 0)  # still in the friend's first two weeks
        self.age_referral()
        count, credit, send = self.reward()
        self.assertEqual(count, 1)
        self.assertEqual(credit.call_args.args[0], "cus_inviter")
        self.assertEqual((credit.call_args.kwargs["amount"], credit.call_args.kwargs["currency"]), (-1200, "gbp"))
        self.assertEqual(send.call_args.args[1], "inviter@example.com")
        self.assertNotIn("friend@example.com", send.call_args.args[3])  # the friend isn't named
        ref = self.referral()
        self.assertEqual((ref.status, ref.reward_pence), ("rewarded", 1200))
        self.assertEqual(self.reward()[0], 0)  # never twice
        self.assertIn("£12.00 in free months", self.client.get("/account").text)

    def test_no_reward_if_friend_cancels_in_hold(self):
        sub_id = self.friend_signup()
        self.friend_pays(sub_id)
        db = self.db
        db.get(Subscription, sub_id).cancel_at = now() + timedelta(days=10)
        db.commit()
        db.close()
        self.age_referral()
        count, credit, _ = self.reward()
        self.assertEqual(count, 0)
        self.assertFalse(credit.called)
        ref = self.referral()
        self.assertEqual(ref.status, "not_rewarded")
        self.assertIn("cancelled", ref.note)

    def test_no_reward_for_same_address(self):
        self.friend_pays(self.friend_signup(postcode="bs1 4st", line1="1 Example Street"))
        self.age_referral()
        self.assertEqual(self.reward()[0], 0)
        self.assertIn("same address", self.referral().note)

    def test_yearly_limit(self):
        self.friend_pays(self.friend_signup())
        self.age_referral()
        with mock.patch("app.referrals.REFERRAL_MAX_PER_YEAR", 0):
            self.assertEqual(self.reward()[0], 0)
        self.assertIn("yearly limit", self.referral().note)

    def test_failed_credit_is_retried(self):
        self.friend_pays(self.friend_signup())
        self.age_referral()
        with mock.patch("stripe.Customer.create_balance_transaction", side_effect=RuntimeError("down")), \
                mock.patch("app.billing.ensure_customer", return_value="cus_inviter"):
            self.assertEqual(referrals.run_rewards(self.db, self.settings), 0)
        self.assertEqual(self.referral().status, "paid")
        self.assertEqual(self.reward()[0], 1)

    def test_a_family_can_only_be_referred_once(self):
        self.friend_signup()
        db = self.db
        friend = db.query(User).filter_by(email="friend@example.com").one()
        self.assertIsNone(referrals.record_signup(db, friend, self.code))
        self.assertEqual(db.query(Referral).count(), 1)
        db.close()

    def test_deleting_account_removes_unfinished_referral(self):
        self.friend_signup()
        token = re.search(r'name="csrf" value="([^"]+)"', self.friend.get("/account").text).group(1)
        self.friend.post("/account/delete", data={"csrf": token, "confirm": "DELETE"})
        db = self.db
        self.assertEqual(db.query(Referral).count(), 0)
        db.close()

    def test_admin_sees_referrals(self):
        self.friend_signup()
        page = self.make_admin().get("/admin/sources").text
        self.assertIn("Recent referrals", page)
        self.assertIn("signed up", page)
        self.assertIn("referral", page)

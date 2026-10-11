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
        self.assertIn("You've been invited", landing)
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
        self.assertNotIn("You've been invited", self.friend.get("/").text)

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


class CustomCodeTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.make_admin()

    def create(self, **data):
        fields = {"code": "homeed-sarah", "label": "Sarah's blog", "percent_off": "20", "months": "3",
                  "max_uses": "", "expires_on": "", "owner_email": ""}
        fields.update(data)
        token = re.search(r'name="csrf" value="([^"]+)"', self.admin.get("/admin/codes").text).group(1)
        return self.admin.post("/admin/codes", data={"csrf": token, **fields}, follow_redirects=False)

    def new_family(self, code=None, typed=None, email="fam@example.com", postcode="M1 1AA"):
        c = TestClient(self.app)
        if code:
            c.get(f"/r/{code}")
        token = re.search(r'name="csrf" value="([^"]+)"', c.get("/signup").text).group(1)
        r = c.post("/signup", data={"csrf": token, "name": "Kim", "email": email, "password": "a long password",
                                    "agree": "true", "plan": "penpal", **({"invite_code": typed} if typed else {})},
                   follow_redirects=False)
        if r.status_code != 303:
            return c, r
        token = re.search(r'name="csrf" value="([^"]+)"', c.get("/children/new").text).group(1)
        r = c.post("/children/new", data={"csrf": token, "first_name": "Ava", "age": "7", "recipient_name": "Ava",
                                          "line1": "9 Other Road", "town": "Leeds", "postcode": postcode,
                                          "plan": "penpal", "guardian": "true"}, follow_redirects=False)
        return c, int(r.headers["location"].rsplit("/", 1)[1])

    def referral(self, email="fam@example.com"):
        db = self.db
        user = db.query(User).filter_by(email=email).one()
        r = db.query(Referral).filter_by(friend_id=user.id).one_or_none()
        if r:
            db.expunge(r)
        source = user.source
        db.close()
        return r, source

    def test_admin_only(self):
        self.assertEqual(self.client.get("/admin/codes", follow_redirects=False).status_code, 303)  # to log in
        self.signup()
        self.assertEqual(self.client.get("/admin/codes").status_code, 404)  # a parent, not the admin

    def test_create_validates(self):
        self.assertEqual(self.create(code="x").status_code, 400)
        self.assertIn("Codes are 3 to 24", self.create(code="x").text)
        self.assertIn("between 1% and 100%", self.create(percent_off="0").text)
        self.assertIn("1 to 12 months", self.create(months="13").text)
        self.assertIn("No account has that email", self.create(owner_email="nobody@example.com").text)
        self.assertEqual(self.create().status_code, 303)
        self.assertIn("already in use", self.create().text)
        page = self.admin.get("/admin/codes").text
        self.assertIn("HOMEED-SARAH", page)
        self.assertIn("20% off your first 3 months", page)

    def test_link_signup_and_coupon(self):
        self.create()
        c = TestClient(self.app)
        self.assertIn("20% off your first 3 months", c.get("/r/homeed-sarah").text)
        c, sub_id = self.new_family(code="HOMEED-SARAH")
        ref, source = self.referral()
        self.assertIsNone(ref.referrer_id)
        self.assertIsNotNone(ref.code_id)
        self.assertEqual(source, "homeed-sarah")
        self.assertIn("20% off your first 3 months", c.get(f"/checkout/{sub_id}").text)
        db = self.db
        user = db.query(User).filter_by(email="fam@example.com").one()
        missing = stripe.InvalidRequestError("No such coupon", "id")
        with mock.patch("stripe.Coupon.retrieve", side_effect=missing), mock.patch("stripe.Coupon.create") as create:
            cid = referrals.coupon_for(self.settings, db, user)
        db.close()
        self.assertEqual(cid, "code-HOMEED-SARAH-20-3")
        self.assertEqual((create.call_args.kwargs["duration"], create.call_args.kwargs["duration_in_months"]),
                         ("repeating", 3))

    def test_typed_code_at_signup(self):
        self.create(code="PTA-STMARYS", percent_off="100", months="1")
        _, bad = self.new_family(typed="NOT-A-CODE")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("recognise that code", bad.text)
        self.new_family(typed="pta-stmarys", email="fam2@example.com")
        ref, _ = self.referral("fam2@example.com")
        self.assertIsNotNone(ref)
        self.assertEqual(referrals.offer_text(100, 1), "your first month free")

    def test_code_box_at_checkout(self):
        self.create()
        c, sub_id = self.new_family()
        page = c.get(f"/checkout/{sub_id}").text
        self.assertIn("Have an invite code?", page)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        c.post(f"/checkout/{sub_id}/code", data={"csrf": token, "code": "homeed-sarah"})
        page = c.get(f"/checkout/{sub_id}").text
        self.assertIn("20% off your first 3 months", page)
        self.assertNotIn("Have an invite code?", page)
        self.assertIsNotNone(self.referral()[0])

    def test_limits(self):
        self.create(max_uses="1")
        self.new_family(code="HOMEED-SARAH")
        c, _ = self.new_family(typed="HOMEED-SARAH", email="fam2@example.com")
        self.assertIn("fully used", _.text)
        self.create(code="OLD-CODE", expires_on="2020-01-01")
        self.assertIn("expired", self.new_family(typed="OLD-CODE", email="fam3@example.com")[1].text)
        self.create(code="PAUSED")
        db = self.db
        from app.models import ReferralCode
        code_id = db.query(ReferralCode).filter_by(code="PAUSED").one().id
        db.close()
        token = re.search(r'name="csrf" value="([^"]+)"', self.admin.get("/admin/codes").text).group(1)
        self.admin.post(f"/admin/codes/{code_id}/toggle", data={"csrf": token})
        self.assertIn("running any more", self.new_family(typed="PAUSED", email="fam4@example.com")[1].text)

    def test_unowned_code_counts_without_reward(self):
        self.create()
        _, sub_id = self.new_family(code="HOMEED-SARAH")
        self.activate(sub_id, stripe_id="sub_fam", event_suffix="fam")
        db = self.db
        db.query(Referral).one().paid_at = now() - timedelta(days=15)
        db.commit()
        db.close()
        with mock.patch("stripe.Customer.create_balance_transaction") as credit:
            referrals.run_rewards(self.db, self.settings)
        self.assertFalse(credit.called)
        self.assertEqual(self.referral()[0].status, "joined")
        self.assertIn(">1</td>", self.admin.get("/admin/codes").text)

    def test_owned_code_rewards_owner_without_yearly_cap(self):
        owner_sub = self.signup(email="sarah@example.com")
        self.activate(owner_sub, stripe_id="sub_sarah", event_suffix="sarah")
        self.create(owner_email="sarah@example.com")
        _, sub_id = self.new_family(code="HOMEED-SARAH")
        self.activate(sub_id, stripe_id="sub_fam", event_suffix="fam")
        db = self.db
        db.query(Referral).one().paid_at = now() - timedelta(days=15)
        db.commit()
        db.close()
        with mock.patch("stripe.Customer.create_balance_transaction") as credit, \
                mock.patch("app.billing.ensure_customer", return_value="cus_sarah"), \
                mock.patch("app.referrals.send_email"), mock.patch("app.referrals.REFERRAL_MAX_PER_YEAR", 0):
            self.assertEqual(referrals.run_rewards(self.db, self.settings), 1)
        self.assertEqual(credit.call_args.args[0], "cus_sarah")
        self.assertEqual(self.referral()[0].status, "rewarded")

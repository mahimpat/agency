"""Gift subscriptions: buying, the certificate, redeeming, monthly letters, carrying on, refunds."""

import re
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from app import gifts, jobs
from app.models import Gift, Subscription, now
from tests.test_app import AppTestCase


class GiftTests(AppTestCase):
    def buy(self, months=6, email="granny@example.com"):
        """Buy a gift through the form, with Stripe Checkout mocked. Returns the gift id."""
        session = SimpleNamespace(id="cs_gift_1", url="https://checkout.stripe.test/pay")
        with mock.patch("stripe.checkout.Session.create", return_value=session) as create:
            r = self.client.post("/gift", data={
                "csrf": self.csrf("/gift"), "months": str(months), "buyer_name": "Granny Rose",
                "buyer_email": email, "child_first_name": "Maya", "message": "Happy birthday!", "agree": "true"},
                follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text[:300])
        self.assertEqual(r.headers["location"], session.url)
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["mode"], "payment")
        self.assertEqual(kwargs["line_items"][0]["price_data"]["currency"], "gbp")
        db = self.db
        gift = db.query(Gift).one()
        self.assertEqual(kwargs["line_items"][0]["price_data"]["unit_amount"], gift.price_pence)
        db.close()
        return gift.id

    def pay(self, gift_id, event_id="evt_gift_paid"):
        with mock.patch("app.gifts.send_email") as send:
            self.webhook({"id": event_id, "object": "event", "type": "checkout.session.completed",
                          "data": {"object": {"object": "checkout.session", "mode": "payment", "payment_status": "paid",
                                              "payment_intent": "pi_gift_1", "metadata": {"gift_id": str(gift_id)}}}})
        return send

    def gift(self, gift_id):
        db = self.db
        gift = db.get(Gift, gift_id)
        db.expunge(gift)
        db.close()
        return gift

    def redeem_and_signup(self, code, client=None):
        client = client or self.client
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/redeem").text).group(1)
        r = client.post("/redeem", data={"csrf": token, "code": code}, follow_redirects=False)
        self.assertEqual(r.headers["location"], "/signup?plan=penpal")
        signup = client.get("/signup").text
        self.assertIn("You're redeeming gift", signup)
        token = re.search(r'name="csrf" value="([^"]+)"', signup).group(1)
        client.post("/signup", data={"csrf": token, "name": "Priya", "email": "parent@example.com",
                                     "password": "a long password", "agree": "true", "plan": "penpal"})
        form = client.get("/children/new").text
        self.assertIn("Start the gift", form)
        self.assertNotIn('name="plan" value="story"', form)
        token = re.search(r'name="csrf" value="([^"]+)"', form).group(1)
        return client.post("/children/new", data={
            "csrf": token, "first_name": "Maya", "age": "9", "recipient_name": "Maya Smith", "line1": "1 Example St",
            "town": "Bristol", "postcode": "BS1 4ST", "plan": "story", "guardian": "true"}, follow_redirects=False)

    def test_gift_pages_are_public(self):
        for path in ["/gift", "/redeem"]:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertIn('href="/gift"', self.client.get("/").text)
        self.assertIn('id="gifts"', self.client.get("/terms").text)

    def test_buy_validates(self):
        r = self.client.post("/gift", data={"csrf": self.csrf("/gift"), "months": "5", "buyer_name": "",
                                            "buyer_email": "nope"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Choose how many months", r.text)
        self.assertIn("valid email", r.text)

    def test_paying_makes_certificate_and_emails_buyer(self):
        gift_id = self.buy(months=6)
        self.assertEqual(self.gift(gift_id).status, "pending")
        send = self.pay(gift_id)
        gift = self.gift(gift_id)
        self.assertEqual(gift.status, "paid")
        self.assertEqual(gift.price_pence, 6900)
        self.assertEqual(gift.stripe_payment_intent, "pi_gift_1")
        self.assertTrue(gift.certificate_path.endswith(".pdf"))
        body = send.call_args.args[3]
        self.assertEqual(send.call_args.args[1], "granny@example.com")
        self.assertIn(gift.code, body)
        link = re.search(r"/gift/certificate/(\S+)", body).group(0)
        r = self.client.get(link)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertEqual(self.client.get("/gift/certificate/forged").status_code, 404)
        # The same payment reported again (webhook retry, thank-you page) changes nothing.
        self.assertFalse(self.pay(gift_id, "evt_gift_paid_again").called)

    def test_thank_you_page_confirms_with_stripe(self):
        gift_id = self.buy()
        paid = mock.Mock(**{"to_dict.return_value": {"payment_status": "paid", "payment_intent": "pi_x"}})
        with mock.patch("stripe.checkout.Session.retrieve", return_value=paid), mock.patch("app.gifts.send_email"):
            r = self.client.get("/gift/thanks?session_id=cs_gift_1")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.gift(gift_id).code, r.text)
        self.assertIn("/gift/certificate/", r.text)
        self.assertEqual(self.client.get("/gift/thanks?session_id=cs_other").status_code, 404)

    def test_certificate_text(self):
        gift = Gift(code="LETTER-ABCD-2345", months=12, price_pence=13200, buyer_name="Granny <Rose>",
                    buyer_email="g@example.com", child_first_name="Maya", message="Love you", expires_at=now())
        html = gifts.certificate_html(self.settings, gift)
        self.assertIn("for Maya", html)
        self.assertIn("12 months", html)
        self.assertIn("Granny &lt;Rose&gt;", html)
        self.assertIn("LETTER-ABCD-2345", html)

    def test_codes(self):
        self.assertEqual(gifts.normalise_code(" letter-abcd 2345 "), "LETTER-ABCD-2345")
        self.assertEqual(gifts.normalise_code("abcd2345"), "LETTER-ABCD-2345")
        self.assertEqual(gifts.normalise_code("abc"), "")

    def test_redeem_without_paying(self):
        gift_id = self.buy()
        code = self.gift(gift_id).code
        r = self.client.post("/redeem", data={"csrf": self.csrf("/redeem"), "code": code})
        self.assertEqual(r.status_code, 400)
        self.assertIn("find that gift code", r.text)

    def test_redeem_flow_starts_pen_pal_with_no_payment(self):
        gift_id = self.buy(months=3)
        self.pay(gift_id)
        code = self.gift(gift_id).code
        r = self.redeem_and_signup(code.lower().replace("-", " "))
        self.assertEqual(r.headers["location"], "/account")
        db = self.db
        sub = db.query(Subscription).one()
        self.assertEqual((sub.plan, sub.status, sub.gift_id, sub.gift_months_left), ("penpal", "active", gift_id, 2))
        self.assertEqual((sub.story_credits, sub.reply_credits), (1, 1))
        self.assertIsNone(sub.stripe_subscription_id)
        db.close()
        self.assertEqual(self.gift(gift_id).status, "redeemed")
        self.assertIn("Gift · Pen Pal", self.client.get("/account").text)
        # The first letter is written without any payment.
        jobs.run_due(self.db, self.settings, self.services)
        self.assertEqual(self.services.written[0]["kind"], "intro")

        # The same code can't be used twice.
        other = TestClient(self.app)
        token = re.search(r'name="csrf" value="([^"]+)"', other.get("/redeem").text).group(1)
        r = other.post("/redeem", data={"csrf": token, "code": code})
        self.assertIn("already been used", r.text)

    def test_logged_in_parent_redeems_for_another_child(self):
        self.signup()
        gift_id = self.buy()
        self.pay(gift_id)
        r = self.client.post("/redeem", data={"csrf": self.csrf("/redeem"), "code": self.gift(gift_id).code},
                             follow_redirects=False)
        self.assertEqual(r.headers["location"], "/children/new")

    def test_login_keeps_the_gift(self):
        self.signup()
        self.client.post("/logout", data={"csrf": self.csrf("/account")})
        gift_id = self.buy()
        self.pay(gift_id)
        self.client.post("/redeem", data={"csrf": self.csrf("/redeem"), "code": self.gift(gift_id).code})
        r = self.client.post("/login", data={"csrf": self.csrf("/login"), "email": "parent@example.com",
                                             "password": "a long password"}, follow_redirects=False)
        self.assertEqual(r.headers["location"], "/children/new")

    def test_expired_code(self):
        gift_id = self.buy()
        self.pay(gift_id)
        db = self.db
        db.get(Gift, gift_id).expires_at = now() - timedelta(days=1)
        db.commit()
        db.close()
        r = self.client.post("/redeem", data={"csrf": self.csrf("/redeem"), "code": self.gift(gift_id).code})
        self.assertIn("expired", r.text)
        jobs.run_due(self.db, self.settings, self.services)
        self.assertEqual(self.gift(gift_id).status, "expired")

    def test_gift_expiring_between_redeem_page_and_child_form(self):
        gift_id = self.buy()
        self.pay(gift_id)
        self.client.post("/redeem", data={"csrf": self.csrf("/redeem"), "code": self.gift(gift_id).code})
        db = self.db
        db.get(Gift, gift_id).status = "refunded"
        db.commit()
        db.close()
        token = self.csrf("/signup")
        self.client.post("/signup", data={"csrf": token, "name": "P", "email": "p@example.com",
                                          "password": "a long password", "agree": "true"})
        r = self.client.post("/children/new", data={
            "csrf": self.csrf("/children/new"), "first_name": "Maya", "age": "9", "recipient_name": "M",
            "line1": "1 St", "town": "Bristol", "postcode": "BS1 4ST", "plan": "penpal", "guardian": "true"},
            follow_redirects=False)
        # The code was removed from the session as the form loaded, so this is now an ordinary paid sign-up.
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/checkout/"))

    def advance(self, days):
        db = self.db
        sub = db.query(Subscription).one()
        sub.next_credit_at = now() - timedelta(minutes=1)
        db.commit()
        db.close()

    def test_monthly_letters_then_reminder_then_end(self):
        gift_id = self.buy(months=3)
        self.pay(gift_id)
        self.redeem_and_signup(self.gift(gift_id).code)
        with mock.patch("app.gifts.send_email") as send:
            self.advance(30)
            gifts.run_gift_months(self.db, self.settings)  # month 2
            self.assertFalse(send.called)
            self.advance(30)
            gifts.run_gift_months(self.db, self.settings)  # month 3, the last: reminder
            self.assertIn("one month left", send.call_args.args[2])
            self.assertIn("/checkout/", send.call_args.args[3])
            self.advance(30)
            gifts.run_gift_months(self.db, self.settings)  # used up
            self.assertIn("has ended", send.call_args.args[2])
        db = self.db
        sub = db.query(Subscription).one()
        self.assertEqual(sub.status, "gift_ended")
        self.assertEqual(sub.story_credits, 3)  # one per month (none spent: no job ran)
        sub_id = sub.id
        db.close()
        self.assertIn("Continue Pen Pal", self.client.get("/account").text)

        # Carrying on: a normal subscription, billed straight away.
        self.client.get("/verify/" + self._verify_token())
        with mock.patch("app.billing.ensure_customer", return_value="cus_1"), \
                mock.patch("stripe.checkout.Session.create",
                           return_value=SimpleNamespace(url="https://checkout.stripe.test/sub")) as create:
            r = self.client.post(f"/checkout/{sub_id}", data={"csrf": self.csrf(f"/checkout/{sub_id}")},
                                 follow_redirects=False)
        self.assertEqual(r.headers["location"], "https://checkout.stripe.test/sub")
        self.assertNotIn("trial_end", create.call_args.kwargs["subscription_data"])
        self.activate(sub_id, stripe_id="sub_after_gift")
        db = self.db
        sub = db.get(Subscription, sub_id)
        self.assertEqual((sub.status, sub.stripe_subscription_id, sub.story_credits), ("active", "sub_after_gift", 4))
        db.close()

    def test_continuing_in_last_month_starts_billing_when_gift_ends(self):
        gift_id = self.buy(months=3)
        self.pay(gift_id)
        self.redeem_and_signup(self.gift(gift_id).code)
        db = self.db
        sub = db.query(Subscription).one()
        sub_id = sub.id
        self.assertTrue(sub.gift_months_left)
        db.close()
        # Not yet: there are paid-for months still to come.
        r = self.client.post(f"/checkout/{sub_id}", data={"csrf": self.csrf(f"/checkout/{sub_id}")},
                             follow_redirects=False)
        self.assertEqual(r.headers["location"], "/account")
        db = self.db
        sub = db.get(Subscription, sub_id)
        sub.gift_months_left = 0
        sub.next_credit_at = now() + timedelta(days=20)
        db.commit()
        db.close()
        self.client.get("/verify/" + self._verify_token())
        self.assertIn("your first payment is on", self.client.get(f"/checkout/{sub_id}").text)
        with mock.patch("app.billing.ensure_customer", return_value="cus_1"), \
                mock.patch("stripe.checkout.Session.create",
                           return_value=SimpleNamespace(url="https://checkout.stripe.test/sub")) as create:
            self.client.post(f"/checkout/{sub_id}", data={"csrf": self.csrf(f"/checkout/{sub_id}")})
        self.assertIn("trial_end", create.call_args.kwargs["subscription_data"])
        # Stripe's £0 trial invoice doesn't add letters: the gift already paid for this month.
        self.webhook({"id": "evt_trial_inv", "object": "event", "type": "invoice.paid",
                      "data": {"object": {"object": "invoice", "billing_reason": "subscription_create", "amount_paid": 0,
                                          "parent": {"subscription_details": {"subscription": "sub_t",
                                                     "metadata": {"subscription_row_id": str(sub_id)}}}}}})
        db = self.db
        self.assertEqual(db.get(Subscription, sub_id).story_credits, 1)
        db.close()

    def _verify_token(self):
        from app.security import make_verify_token
        db = self.db
        user = db.query(Subscription).one().user
        token = make_verify_token(self.settings.secret_key, user.id, user.email)
        db.close()
        return token

    def test_refund_before_redeeming_cancels_code(self):
        gift_id = self.buy()
        self.pay(gift_id)
        self.webhook({"id": "evt_refund", "object": "event", "type": "charge.refunded",
                      "data": {"object": {"object": "charge", "payment_intent": "pi_gift_1"}}})
        gift = self.gift(gift_id)
        self.assertEqual(gift.status, "refunded")
        r = self.client.post("/redeem", data={"csrf": self.csrf("/redeem"), "code": gift.code})
        self.assertEqual(r.status_code, 400)

    def test_old_gift_details_are_forgotten(self):
        gift_id = self.buy()
        self.pay(gift_id)
        db = self.db
        gift = db.get(Gift, gift_id)
        gift.status = "expired"
        gift.expires_at = now() - timedelta(days=400)
        db.commit()
        db.close()
        gifts.run_gift_months(self.db, self.settings)
        gift = self.gift(gift_id)
        self.assertEqual((gift.buyer_email, gift.buyer_name, gift.message), ("", "", ""))
        self.assertIsNone(gift.certificate_path)

    def test_redeem_is_rate_limited(self):
        token = self.csrf("/redeem")
        codes = [self.client.post("/redeem", data={"csrf": token, "code": "LETTER-AAAA-BBBB"}).status_code
                 for _ in range(12)]
        self.assertIn(429, codes)

    def test_admin_lists_gifts(self):
        gift_id = self.buy()
        self.pay(gift_id)
        self.assertIn(self.gift(gift_id).code, self.make_admin().get("/admin").text)

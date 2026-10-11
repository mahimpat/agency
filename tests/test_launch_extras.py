"""Sign-up sources, subscription rules (cancel, reminders, pre-payment summary), scheduled gift emails,
the Christmas notice, and the pen pal list."""

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import gifts, reminders
from app.config import FIGURE_ORDER
from app.models import Gift, Subscription, User, now
from app.seasonal import christmas_notice
from letters import render
from tests.test_app import AppTestCase

FIGURES = Path(__file__).resolve().parent.parent / "letters" / "figures"


class SourceTests(AppTestCase):
    def user(self):
        db = self.db
        user = db.query(User).filter_by(email="parent@example.com").one()
        db.expunge(user)
        db.close()
        return user

    def test_first_link_is_recorded_on_signup(self):
        self.client.get("/?ref=FB Home-Ed!&utm_medium=social&utm_campaign=Launch 1")
        self.client.get("/gift?ref=somewhere-else")  # first visit wins
        self.signup()
        user = self.user()
        self.assertEqual((user.source, user.campaign, user.landing), ("fb-home-ed", "social/launch-1", "/"))

    def test_utm_source_and_no_source(self):
        self.client.get("/signup?utm_source=Meta")
        self.signup()
        self.assertEqual(self.user().source, "meta")

    def test_report_counts_paying_families(self):
        self.client.get("/?ref=mumsnet")
        sub_id = self.signup()
        self.activate(sub_id)
        admin = self.make_admin()
        page = admin.get("/admin/sources").text
        self.assertIn("mumsnet", page)
        self.assertIn("£12.00", page)
        self.assertIn("100%", page)
        self.assertEqual(self.client.get("/admin/sources").status_code, 404)


class SubscriptionRuleTests(AppTestCase):
    def paid_sub(self):
        sub_id = self.signup()
        self.activate(sub_id)
        return sub_id

    def sub(self, sub_id):
        db = self.db
        sub = db.get(Subscription, sub_id)
        db.expunge(sub)
        db.close()
        return sub

    def test_checkout_shows_key_terms_before_paying(self):
        sub_id = self.signup()
        page = self.client.get(f"/checkout/{sub_id}").text
        for words in ("Before you pay", "£12 a month", "until you cancel", "One click", "every 6 months", "14-day"):
            self.assertIn(words, page)

    def test_first_payment_sets_start(self):
        sub_id = self.paid_sub()
        self.assertIsNotNone(self.sub(sub_id).started_at)

    def test_one_click_cancel_and_undo(self):
        sub_id = self.paid_sub()
        self.assertIn(f"/subscriptions/{sub_id}/cancel", self.client.get("/account").text)
        ends = int((now() + timedelta(days=20)).timestamp())
        with mock.patch("stripe.Subscription.modify", return_value={"id": "sub_123", "cancel_at": ends}) as modify, \
                mock.patch("app.web.send_email") as send:
            r = self.client.post(f"/subscriptions/{sub_id}/cancel",
                                 data={"csrf": self.csrf(f"/subscriptions/{sub_id}/cancel")}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        modify.assert_called_once_with("sub_123", cancel_at_period_end=True)
        self.assertIn("cancelled", send.call_args.args[2])
        self.assertIsNotNone(self.sub(sub_id).cancel_at)
        self.assertIn("Keep it going", self.client.get("/account").text)

        with mock.patch("stripe.Subscription.modify", return_value={"id": "sub_123", "cancel_at": None}) as modify:
            self.client.post(f"/subscriptions/{sub_id}/keep", data={"csrf": self.csrf("/account")})
        modify.assert_called_once_with("sub_123", cancel_at_period_end=False)
        self.assertIsNone(self.sub(sub_id).cancel_at)

    def test_cancel_uses_period_end_from_newer_api(self):
        sub_id = self.paid_sub()
        end = int((now() + timedelta(days=12)).timestamp())
        result = {"id": "sub_123", "cancel_at": None, "items": {"data": [{"current_period_end": end}]}}
        with mock.patch("stripe.Subscription.modify", return_value=result), mock.patch("app.web.send_email"):
            self.client.post(f"/subscriptions/{sub_id}/cancel", data={"csrf": self.csrf(f"/subscriptions/{sub_id}/cancel")})
        self.assertIsNotNone(self.sub(sub_id).cancel_at)

    def test_cannot_cancel_someone_elses(self):
        sub_id = self.paid_sub()
        self.client.post("/logout", data={"csrf": self.csrf("/account")})
        self.signup(email="other@example.com")
        self.assertEqual(self.client.get(f"/subscriptions/{sub_id}/cancel").status_code, 404)

    def test_portal_cancellation_is_tracked(self):
        sub_id = self.paid_sub()
        ends = int((now() + timedelta(days=9)).timestamp())
        self.webhook({"id": "evt_upd_cancel", "object": "event", "type": "customer.subscription.updated",
                      "data": {"object": {"object": "subscription", "id": "sub_123", "status": "active",
                                          "cancel_at": ends, "metadata": {"subscription_row_id": str(sub_id)}}}})
        self.assertIsNotNone(self.sub(sub_id).cancel_at)

    def test_six_monthly_reminder(self):
        sub_id = self.paid_sub()
        with mock.patch("app.reminders.send_email") as send:
            self.assertEqual(reminders.run_reminders(self.db, self.settings), 0)  # too soon
            db = self.db
            db.get(Subscription, sub_id).started_at = now() - timedelta(days=183)
            db.commit()
            db.close()
            self.assertEqual(reminders.run_reminders(self.db, self.settings), 1)
            body = send.call_args.args[3]
            self.assertIn("£12 a month", body)
            self.assertIn("/account", body)
            self.assertIn("Cancel subscription", body)
            self.assertEqual(reminders.run_reminders(self.db, self.settings), 0)  # not again for 6 months
            db = self.db
            sub = db.get(Subscription, sub_id)
            sub.last_reminder_at = now() - timedelta(days=183)
            sub.cancel_at = now() + timedelta(days=5)
            db.commit()
            db.close()
            self.assertEqual(reminders.run_reminders(self.db, self.settings), 0)  # already cancelled


class ScheduledGiftTests(AppTestCase):
    def buy(self, **extra):
        session = SimpleNamespace(id="cs_g", url="https://checkout.stripe.test/pay")
        with mock.patch("stripe.checkout.Session.create", return_value=session):
            return self.client.post("/gift", data={
                "csrf": self.csrf("/gift"), "months": "3", "buyer_name": "Granny", "buyer_email": "granny@example.com",
                "child_first_name": "Maya", "message": "Love you", "agree": "true", **extra}, follow_redirects=False)

    def pay(self):
        db = self.db
        gift_id = db.query(Gift).one().id
        db.close()
        self.webhook({"id": "evt_paid", "object": "event", "type": "checkout.session.completed",
                      "data": {"object": {"object": "checkout.session", "mode": "payment", "payment_status": "paid",
                                          "payment_intent": "pi_1", "metadata": {"gift_id": str(gift_id)}}}})
        return gift_id

    def gift(self):
        db = self.db
        gift = db.query(Gift).one()
        db.expunge(gift)
        db.close()
        return gift

    def test_needs_email_and_date(self):
        r = self.buy(delivery="email", recipient_email="", send_on="")
        self.assertEqual(r.status_code, 400)
        self.assertIn("family's email", r.text)
        self.assertIn("Choose the day", r.text)
        r = self.buy(delivery="email", recipient_email="mum@example.com", send_on=str(date.today() - timedelta(days=1)))
        self.assertEqual(r.status_code, 400)

    def test_sent_to_family_on_the_day(self):
        self.client.get("/gift?utm_source=xmas-ad")
        day = date.today() + timedelta(days=10)
        self.assertEqual(self.buy(delivery="email", recipient_email="Mum@Example.com", send_on=str(day)).status_code, 303)
        self.assertEqual(self.gift().source, "xmas-ad")
        with mock.patch("app.gifts.send_email") as send:
            self.pay()
        self.assertEqual([c.args[1] for c in send.call_args_list], ["granny@example.com"])
        self.assertIn("mum@example.com", send.call_args.args[3])
        with mock.patch("app.gifts.send_email") as send:
            gifts.run_gift_months(self.db, self.settings)
        self.assertFalse(send.called)  # not yet
        db = self.db
        db.query(Gift).one().send_on = now() - timedelta(minutes=1)
        db.commit()
        db.close()
        with mock.patch("app.gifts.send_email") as send:
            gifts.run_gift_months(self.db, self.settings)
            gifts.run_gift_months(self.db, self.settings)  # only once
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1], "mum@example.com")
        self.assertIn("A gift for Maya from Granny", send.call_args.args[2])
        self.assertIn("/redeem", send.call_args.args[3])
        gift = self.gift()
        self.assertEqual(gift.recipient_email, "")  # deleted once sent
        self.assertIsNotNone(gift.sent_to_recipient_at)

    def test_today_is_sent_straight_away(self):
        self.buy(delivery="email", recipient_email="mum@example.com", send_on=str(now().date()))
        with mock.patch("app.gifts.send_email") as send:
            self.pay()
        self.assertEqual(sorted(c.args[1] for c in send.call_args_list), ["granny@example.com", "mum@example.com"])

    def test_send_to_me_ignores_family_email(self):
        self.buy(delivery="me", recipient_email="mum@example.com", send_on=str(date.today()))
        gift = self.gift()
        self.assertEqual((gift.recipient_email, gift.send_on), ("", None))


class ChristmasTests(AppTestCase):
    def test_notice_dates(self):
        self.assertIsNone(christmas_notice(date(2026, 10, 31)))
        self.assertIsNone(christmas_notice(date(2026, 12, 26)))
        self.assertTrue(christmas_notice(date(2026, 11, 1))["in_time"])
        self.assertIn("Thursday 10 December", christmas_notice(date(2026, 12, 10))["text"])
        self.assertFalse(christmas_notice(date(2026, 12, 11))["in_time"])
        with mock.patch.dict("os.environ", {"CHRISTMAS_LAST_ORDER": "12-08"}):
            self.assertFalse(christmas_notice(date(2026, 12, 9))["in_time"])

    def test_shown_on_pages_in_season(self):
        with mock.patch("app.web.models_now", return_value=now().replace(year=2026, month=11, day=20)):
            for path in ("/", "/gift", "/signup"):
                self.assertIn("Christmas", self.client.get(path).text, path)
        with mock.patch("app.web.models_now", return_value=now().replace(year=2026, month=10, day=20)):
            self.assertNotIn("sign up by", self.client.get("/gift").text)


class PenPalListTests(AppTestCase):
    def test_every_pen_pal_has_a_fact_sheet_and_stationery(self):
        self.assertIn("samuel-pepys", FIGURE_ORDER)
        self.assertEqual(len(FIGURE_ORDER), len(set(FIGURE_ORDER)))
        for figure in FIGURE_ORDER:
            self.assertTrue((FIGURES / f"{figure}.md").exists(), figure)
            self.assertIn(figure, render.THEMES)
        sheet = (FIGURES / "samuel-pepys.md").read_text()
        for section in ("## Write from", "## Verified facts", "## Handle with care", "Pudding Lane"):
            self.assertIn(section, sheet)

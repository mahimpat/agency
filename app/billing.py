"""Stripe subscriptions: Checkout to subscribe, the Customer Portal to manage or
cancel, and webhooks that keep our records in step and grant letter credits.

Credits come from *paid invoices*, so a letter is only ever written for a
period that has been paid for.
"""

import logging
from datetime import datetime, timedelta, timezone

import stripe
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import PLANS, Settings
from .models import ProcessedEvent, Subscription, User, now

log = logging.getLogger("letters.billing")


def _configure(settings: Settings) -> None:
    if not settings.stripe_secret_key:
        raise RuntimeError("Set STRIPE_SECRET_KEY to take payments.")
    stripe.api_key = settings.stripe_secret_key


def ensure_customer(settings: Settings, db: Session, user: User) -> str:
    _configure(settings)
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email, name=user.name, metadata={"user_id": str(user.id)})
        user.stripe_customer_id = customer.id
        db.commit()
    return user.stripe_customer_id


def can_checkout(sub: Subscription) -> bool:
    """Not paying yet, or a gift that has ended or is in its last month (so the family can carry on)."""
    if sub.status in ("pending", "canceled", "incomplete_expired", "gift_ended"):
        return True
    return bool(sub.gift_id and not sub.stripe_subscription_id and sub.status == "active"
                and sub.gift_months_left == 0)


def gift_trial_end(sub: Subscription):
    """When continuing during a gift's last month, the first charge waits until the gift runs out.
    Stripe needs a trial end at least 48 hours away; closer than that, billing just starts now."""
    if sub.gift_id and sub.status == "active" and sub.next_credit_at and \
            sub.next_credit_at > now() + timedelta(hours=49):
        return sub.next_credit_at
    return None


def checkout_url(settings: Settings, db: Session, user: User, sub: Subscription) -> str:
    """Start Stripe Checkout for one child's subscription and return the URL to send the parent to."""
    price = settings.stripe_prices.get(sub.plan)
    if not price:
        raise RuntimeError(f"Set {PLANS[sub.plan]['price_env']} to the Stripe price ID for this plan.")
    customer = ensure_customer(settings, db, user)
    metadata = {"subscription_row_id": str(sub.id), "child_id": str(sub.child_id)}
    subscription_data = {"metadata": metadata}
    trial_end = gift_trial_end(sub)
    if trial_end:
        subscription_data["trial_end"] = int(trial_end.replace(tzinfo=timezone.utc).timestamp())
    # A family invited by a friend gets money off their first month. Stripe allows either a set
    # discount or customer-entered promotion codes, not both.
    from . import referrals
    offer = ({"discounts": [{"coupon": referrals.coupon_for(settings, db, user)}]}
             if referrals.discount_applies(db, user, sub)
             else {"allow_promotion_codes": True})
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer,
        line_items=[{"price": price, "quantity": 1}],
        client_reference_id=str(sub.id),
        metadata=metadata,
        subscription_data=subscription_data,
        **offer,
        success_url=f"{settings.base_url}/account?checkout=success",
        cancel_url=f"{settings.base_url}/account?checkout=cancelled",
    )
    return session.url


def portal_url(settings: Settings, db: Session, user: User) -> str:
    """Stripe's hosted page where parents update cards, see invoices and cancel."""
    customer = ensure_customer(settings, db, user)
    portal = stripe.billing_portal.Session.create(customer=customer, return_url=f"{settings.base_url}/account")
    return portal.url


def _from_timestamp(value) -> datetime | None:
    return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None) if value else None


def set_cancel_at_period_end(settings: Settings, sub: Subscription, cancel: bool) -> datetime | None:
    """One-click cancel (or undo): letters continue until the end of the month already paid for.
    Returns when the subscription ends, or None if it will carry on."""
    _configure(settings)
    if not sub.stripe_subscription_id:
        raise RuntimeError("This subscription isn't billed through Stripe.")
    result = stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=cancel)
    result = result.to_dict() if hasattr(result, "to_dict") else result
    sub.cancel_at = _from_timestamp(result.get("cancel_at")) if cancel else None
    if cancel and not sub.cancel_at:
        # Newer API versions keep the period end on the subscription item.
        items = (result.get("items") or {}).get("data") or [{}]
        sub.cancel_at = _from_timestamp(result.get("current_period_end") or items[0].get("current_period_end"))
    return sub.cancel_at


def cancel_all(settings: Settings, user: User) -> None:
    """Cancel every live Stripe subscription (used when a parent deletes their account)."""
    _configure(settings)
    for sub in user.subscriptions:
        if sub.stripe_subscription_id and sub.status not in {"canceled", "incomplete_expired"}:
            try:
                stripe.Subscription.cancel(sub.stripe_subscription_id)
            except stripe.InvalidRequestError:
                log.warning("Subscription %s was already gone at Stripe", sub.stripe_subscription_id)


def parse_event(settings: Settings, payload: bytes, signature: str | None) -> dict:
    """Verify the webhook signature and return the event as a plain dict."""
    if not settings.stripe_webhook_secret:
        raise RuntimeError("Set STRIPE_WEBHOOK_SECRET.")
    event = stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    return event.to_dict()


# --- Webhook handling -------------------------------------------------------
# Stripe moved some fields between API versions, so these read both places.

def _invoice_subscription(invoice: dict) -> tuple[str | None, dict]:
    details = (invoice.get("parent") or {}).get("subscription_details") or invoice.get("subscription_details") or {}
    sub_id = details.get("subscription") or invoice.get("subscription")
    if isinstance(sub_id, dict):
        sub_id = sub_id.get("id")
    return sub_id, details.get("metadata") or {}


def _find_subscription(db: Session, stripe_id: str | None, metadata: dict) -> Subscription | None:
    row_id = metadata.get("subscription_row_id")
    if row_id and str(row_id).isdigit():
        sub = db.get(Subscription, int(row_id))
        if sub:
            return sub
    if stripe_id:
        return db.scalar(select(Subscription).where(Subscription.stripe_subscription_id == stripe_id))
    return None


def grant_credits(sub: Subscription) -> None:
    plan = PLANS[sub.plan]
    sub.story_credits += plan["story"]
    if plan["reply"]:
        sub.reply_credits = min(sub.reply_credits + plan["reply"], 2)  # don't stockpile
        sub.reply_credit_at = now()


def _subscription_price(subscription: dict) -> str | None:
    items = (subscription.get("items") or {}).get("data") or []
    price = items[0].get("price") if items else None
    return price.get("id") if isinstance(price, dict) else price


def _plan_for(prices: dict, price_id: str | None) -> str | None:
    return next((plan for plan, pid in prices.items() if pid and pid == price_id), None)


def handle_event(db: Session, event: dict, settings_prices: dict | None = None, gift_paid=None) -> str:
    """settings_prices maps plan -> Stripe price ID, used to follow plan switches.
    gift_paid(gift_id, payment_intent) is called when a one-off gift payment completes."""
    # Apply one Stripe event. Returns a short description (for logs and tests).
    if db.get(ProcessedEvent, event["id"]):
        return "duplicate"
    kind = event["type"]
    obj = event["data"]["object"]
    outcome = "ignored"

    if kind == "checkout.session.completed" and obj.get("mode") == "payment":
        gift_id = (obj.get("metadata") or {}).get("gift_id")
        if gift_id and gift_paid and obj.get("payment_status") == "paid":
            gift_paid(int(gift_id), obj.get("payment_intent"))
            outcome = f"gift {gift_id} paid"

    elif kind == "charge.refunded":
        from .gifts import handle_refund
        outcome = handle_refund(db, obj.get("payment_intent"))

    elif kind == "checkout.session.completed" and obj.get("mode") == "subscription":
        metadata = obj.get("metadata") or {}
        if obj.get("client_reference_id") and "subscription_row_id" not in metadata:
            metadata = {**metadata, "subscription_row_id": obj["client_reference_id"]}
        sub = _find_subscription(db, obj.get("subscription"), metadata)
        if sub:
            sub.stripe_subscription_id = obj.get("subscription")
            if sub.status in ("pending", "gift_ended", "canceled", "incomplete_expired") or sub.gift_id:
                sub.status = "active"  # includes a family continuing after a gift
            outcome = f"linked subscription {sub.id}"

    elif kind == "invoice.paid":
        stripe_id, metadata = _invoice_subscription(obj)
        sub = _find_subscription(db, stripe_id, metadata)
        # A family continuing in a gift's last month gets a free trial invoice first: the gift
        # already paid for that month's letters, so only real payments add credits for them.
        # A trial invoice has nothing on it (subtotal 0); a month made free by a 100% code still has
        # the plan's price before the discount, so it counts as a paid month.
        subtotal = obj.get("subtotal", obj.get("amount_paid"))
        trial_for_gift = bool(sub and sub.gift_id and subtotal == 0)
        if sub and obj.get("billing_reason") in {"subscription_create", "subscription_cycle", None} \
                and not trial_for_gift:
            if stripe_id and not sub.stripe_subscription_id:
                sub.stripe_subscription_id = stripe_id
            sub.status = "active"
            if not sub.started_at:
                sub.started_at = now()  # paid billing starts: the 6-monthly reminders count from here
                from .referrals import on_first_payment
                on_first_payment(db, sub)
            grant_credits(sub)
            outcome = f"credited subscription {sub.id}"

    elif kind == "invoice.payment_failed":
        stripe_id, metadata = _invoice_subscription(obj)
        sub = _find_subscription(db, stripe_id, metadata)
        if sub:
            sub.status = "past_due"
            outcome = f"past_due {sub.id}"

    elif kind in {"customer.subscription.updated", "customer.subscription.deleted"}:
        sub = _find_subscription(db, obj.get("id"), obj.get("metadata") or {})
        if sub:
            sub.stripe_subscription_id = obj.get("id")
            sub.status = "canceled" if kind.endswith("deleted") else obj.get("status", sub.status)
            # A cancellation booked for the end of the paid month (from our account page or Stripe's portal).
            sub.cancel_at = None if kind.endswith("deleted") else _from_timestamp(obj.get("cancel_at"))
            outcome = f"status {sub.status} {sub.id}"
            # The parent may have switched plan in the billing portal: follow the price.
            new_plan = _plan_for(settings_prices, _subscription_price(obj)) if settings_prices else None
            if new_plan and new_plan != sub.plan:
                outcome += f", plan {sub.plan} -> {new_plan}"
                sub.plan = new_plan
                if not PLANS[new_plan]["reply"]:
                    sub.reply_credits = 0

    db.add(ProcessedEvent(id=event["id"]))
    db.commit()
    return outcome

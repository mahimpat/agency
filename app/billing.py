"""Stripe subscriptions: Checkout to subscribe, the Customer Portal to manage or
cancel, and webhooks that keep our records in step and grant letter credits.

Credits come from *paid invoices*, so a letter is only ever written for a
period that has been paid for.
"""

import logging

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


def checkout_url(settings: Settings, db: Session, user: User, sub: Subscription) -> str:
    """Start Stripe Checkout for one child's subscription and return the URL to send the parent to."""
    price = settings.stripe_prices.get(sub.plan)
    if not price:
        raise RuntimeError(f"Set {PLANS[sub.plan]['price_env']} to the Stripe price ID for this plan.")
    customer = ensure_customer(settings, db, user)
    metadata = {"subscription_row_id": str(sub.id), "child_id": str(sub.child_id)}
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer,
        line_items=[{"price": price, "quantity": 1}],
        client_reference_id=str(sub.id),
        metadata=metadata,
        subscription_data={"metadata": metadata},
        allow_promotion_codes=True,
        success_url=f"{settings.base_url}/account?checkout=success",
        cancel_url=f"{settings.base_url}/account?checkout=cancelled",
    )
    return session.url


def portal_url(settings: Settings, db: Session, user: User) -> str:
    """Stripe's hosted page where parents update cards, see invoices and cancel."""
    customer = ensure_customer(settings, db, user)
    portal = stripe.billing_portal.Session.create(customer=customer, return_url=f"{settings.base_url}/account")
    return portal.url


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


def handle_event(db: Session, event: dict) -> str:
    """Apply one Stripe event. Returns a short description (for logs and tests)."""
    if db.get(ProcessedEvent, event["id"]):
        return "duplicate"
    kind = event["type"]
    obj = event["data"]["object"]
    outcome = "ignored"

    if kind == "checkout.session.completed" and obj.get("mode") == "subscription":
        metadata = obj.get("metadata") or {}
        if obj.get("client_reference_id") and "subscription_row_id" not in metadata:
            metadata = {**metadata, "subscription_row_id": obj["client_reference_id"]}
        sub = _find_subscription(db, obj.get("subscription"), metadata)
        if sub:
            sub.stripe_subscription_id = obj.get("subscription")
            if sub.status == "pending":
                sub.status = "active"
            outcome = f"linked subscription {sub.id}"

    elif kind == "invoice.paid":
        stripe_id, metadata = _invoice_subscription(obj)
        sub = _find_subscription(db, stripe_id, metadata)
        if sub and obj.get("billing_reason") in {"subscription_create", "subscription_cycle", None}:
            if stripe_id and not sub.stripe_subscription_id:
                sub.stripe_subscription_id = stripe_id
            sub.status = "active"
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
            outcome = f"status {sub.status} {sub.id}"

    db.add(ProcessedEvent(id=event["id"]))
    db.commit()
    return outcome

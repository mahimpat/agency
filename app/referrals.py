"""Refer a friend.

Every parent gets an invite link (/r/CODE). A new family who signs up through it gets
REFERRAL_FRIEND_PERCENT off their first month (a Stripe coupon applied at checkout). Once that family
has paid and kept their subscription for REFERRAL_HOLD_DAYS, the referrer gets one month of their own
plan free, as a credit on their Stripe balance, so their next bill is reduced to nothing.

Guards: only new families (no earlier paid subscription), no referring yourself (same account, or a
child at the same address), one referral per family, and at most REFERRAL_MAX_PER_YEAR rewards each.
"""

import logging
import secrets
from datetime import timedelta

import stripe
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import (PLANS, REFERRAL_FRIEND_PERCENT, REFERRAL_HOLD_DAYS, REFERRAL_MAX_PER_YEAR, Settings)
from .emails import send_email
from .models import Referral, Subscription, User, now

log = logging.getLogger("letters.referrals")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def code_for(db: Session, user: User) -> str:
    """The parent's invite code, made the first time it's needed."""
    if not user.referral_code:
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
            if not db.scalar(select(User).where(User.referral_code == code)):
                break
        user.referral_code = code
        db.commit()
    return user.referral_code


def link_for(settings: Settings, db: Session, user: User) -> str:
    return f"{settings.base_url}/r/{code_for(db, user)}"


def find_referrer(db: Session, code: str) -> User | None:
    code = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:16]
    return db.scalar(select(User).where(User.referral_code == code)) if code else None


def record_signup(db: Session, friend: User, code: str) -> Referral | None:
    """Called when a new account is created with an invite code in the session."""
    referrer = find_referrer(db, code)
    if not referrer or referrer.id == friend.id or friend.referred_by_id:
        return None
    friend.referred_by_id = referrer.id
    referral = Referral(referrer_id=referrer.id, friend_id=friend.id)
    db.add(referral)
    db.commit()
    return referral


def _referral(db: Session, friend: User) -> Referral | None:
    return db.scalar(select(Referral).where(Referral.friend_id == friend.id))


def discount_applies(db: Session, friend: User) -> bool:
    """A referred family gets the discount on their first paid subscription only."""
    referral = _referral(db, friend)
    if not referral or referral.status != "signed_up":
        return False
    return not any(sub.started_at for sub in friend.subscriptions)


def coupon_id(settings: Settings) -> str:
    """The Stripe coupon for the friend's discount, created the first time it's used."""
    stripe.api_key = settings.stripe_secret_key
    cid = f"referral-{REFERRAL_FRIEND_PERCENT}-once"
    try:
        stripe.Coupon.retrieve(cid)
    except stripe.InvalidRequestError:
        stripe.Coupon.create(id=cid, percent_off=REFERRAL_FRIEND_PERCENT, duration="once",
                             name=f"A friend's invite: {REFERRAL_FRIEND_PERCENT}% off your first month")
    return cid


def on_first_payment(db: Session, sub: Subscription) -> None:
    """The friend has paid: start the hold period before the referrer is rewarded."""
    referral = db.scalar(select(Referral).where(Referral.friend_id == sub.user_id))
    if referral and referral.status == "signed_up":
        referral.status = "paid"
        referral.paid_at = now()
        referral.friend_sub_id = sub.id


def _address_key(child) -> str:
    return ("".join(child.postcode.split()).upper(), "".join(child.line1.split()).lower())


def _why_not(db: Session, referral: Referral, referrer: User | None) -> str:
    if not referrer:
        return "the inviting account has been closed"
    friend_sub = db.get(Subscription, referral.friend_sub_id) if referral.friend_sub_id else None
    if not friend_sub or friend_sub.status not in ("active", "trialing") or friend_sub.cancel_at:
        return f"the new family cancelled within {REFERRAL_HOLD_DAYS} days"
    friend = db.get(User, referral.friend_id)
    if friend and {_address_key(c) for c in friend.children} & {_address_key(c) for c in referrer.children}:
        return "same address as the inviting family"
    year_ago = now() - timedelta(days=365)
    rewarded = db.scalars(select(Referral).where(Referral.referrer_id == referrer.id, Referral.status == "rewarded",
                                                 Referral.rewarded_at >= year_ago)).all()
    if len(rewarded) >= REFERRAL_MAX_PER_YEAR:
        return f"the yearly limit of {REFERRAL_MAX_PER_YEAR} rewards has been reached"
    return ""


def reward_pence(referrer: User) -> int:
    """One month of the referrer's own plan (Pen Pal if they have none yet, e.g. a gift family)."""
    plans = [sub.plan for sub in referrer.subscriptions if sub.started_at or sub.gift_id]
    return max((PLANS[p]["pence"] for p in plans), default=PLANS["penpal"]["pence"])


def run_rewards(db: Session, settings: Settings) -> int:
    """Daily: reward referrals whose new family has paid and stayed for the hold period."""
    from .billing import ensure_customer
    due = db.scalars(select(Referral).where(Referral.status == "paid",
                                            Referral.paid_at <= now() - timedelta(days=REFERRAL_HOLD_DAYS))).all()
    rewarded = 0
    for referral in due:
        referrer = db.get(User, referral.referrer_id)
        reason = _why_not(db, referral, referrer)
        if reason:
            referral.status, referral.note = "not_rewarded", reason
            db.commit()
            continue
        amount = reward_pence(referrer)
        try:
            customer = ensure_customer(settings, db, referrer)
            stripe.Customer.create_balance_transaction(
                customer, amount=-amount, currency="gbp",
                description=f"Thank you for inviting a friend (referral {referral.id})",
                idempotency_key=f"referral-reward-{referral.id}")
        except Exception as e:  # noqa: BLE001 - try again tomorrow
            log.exception("Referral reward %s failed", referral.id)
            referral.note = f"credit failed, will retry: {str(e)[:200]}"
            db.commit()
            continue
        referral.status, referral.reward_pence, referral.rewarded_at, referral.note = "rewarded", amount, now(), ""
        db.commit()
        rewarded += 1
        send_email(settings, referrer.email, "Thank you! Your next month of letters is free",
                   f"Hello {referrer.name},\n\nA family you invited has joined Letters from History. Thank you! "
                   f"We've added £{amount / 100:.2f} to your account, so your next month is free. It comes off your "
                   "next bill automatically.\n\n"
                   f"Your invite link, if you'd like to share it again: {link_for(settings, db, referrer)}\n\n"
                   "Letters from History")
    return rewarded


def stats(db: Session, user: User) -> dict:
    rows = db.scalars(select(Referral).where(Referral.referrer_id == user.id)).all()
    return {"joined": sum(1 for r in rows if r.status in ("paid", "rewarded")),
            "waiting": sum(1 for r in rows if r.status == "paid"),
            "rewarded": sum(1 for r in rows if r.status == "rewarded"),
            "credit_pence": sum(r.reward_pence for r in rows if r.status == "rewarded")}

"""Refer a friend.

Every parent gets an invite link (/r/CODE). A new family who signs up through it gets
REFERRAL_FRIEND_PERCENT off their first month (a Stripe coupon applied at checkout). Once that family
has paid and kept their subscription for REFERRAL_HOLD_DAYS, the referrer gets one month of their own
plan free, as a credit on their Stripe balance, so their next bill is reduced to nothing.

Guards: only new families (no earlier paid subscription), no referring yourself (same account, or a
child at the same address), one referral per family, and at most REFERRAL_MAX_PER_YEAR rewards each.
"""

import logging
import re
import secrets
from datetime import timedelta

import stripe
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import (PLANS, REFERRAL_FRIEND_PERCENT, REFERRAL_HOLD_DAYS, REFERRAL_MAX_PER_YEAR, Settings)
from .emails import send_email
from .models import Referral, ReferralCode, Subscription, User, now

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


def normalise(code: str) -> str:
    return re.sub(r"[^A-Z0-9-]", "", (code or "").upper()).strip("-")[:24]


def find_referrer(db: Session, code: str) -> User | None:
    code = normalise(code).replace("-", "")[:16]
    return db.scalar(select(User).where(User.referral_code == code)) if code else None


def find_custom(db: Session, code: str) -> ReferralCode | None:
    code = normalise(code)
    return db.scalar(select(ReferralCode).where(ReferralCode.code == code)) if code else None


def uses(db: Session, rc: ReferralCode) -> int:
    """Sign-ups with this code."""
    return db.scalar(select(func.count()).select_from(Referral).where(Referral.code_id == rc.id)) or 0


def custom_problem(db: Session, rc: ReferralCode) -> str:
    if not rc.active:
        return "That code isn't running any more."
    if rc.expires_at and rc.expires_at < now():
        return "That code has expired."
    if rc.max_uses is not None and uses(db, rc) >= rc.max_uses:
        return "That code has been fully used."
    return ""


def resolve(db: Session, code: str) -> tuple[User | None, ReferralCode | None, str]:
    """What a code means: (family to reward or None, custom code or None, problem for the parent)."""
    if not normalise(code):
        return None, None, "Enter a code."
    rc = find_custom(db, code)
    if rc:
        problem = custom_problem(db, rc)
        if problem:
            return None, None, problem
        return (db.get(User, rc.owner_user_id) if rc.owner_user_id else None), rc, ""
    user = find_referrer(db, code)
    if user:
        return user, None, ""
    return None, None, "We don't recognise that code. Please check it and try again."


def offer_text(percent: int, months: int) -> str:
    if percent >= 100:
        return "your first month free" if months == 1 else f"your first {months} months free"
    return f"{percent}% off your first month" if months == 1 else f"{percent}% off your first {months} months"


def offer_for_code(db: Session, code: str) -> str:
    _, rc, problem = resolve(db, code)
    if problem:
        return ""
    return offer_text(rc.percent_off, rc.months) if rc else offer_text(REFERRAL_FRIEND_PERCENT, 1)


def record_signup(db: Session, friend: User, code: str) -> Referral | None:
    """A new family used an invite link or code. Only families who haven't paid before qualify."""
    referrer, rc, problem = resolve(db, code)
    if problem or (referrer and referrer.id == friend.id) or _referral(db, friend):
        return None
    if any(sub.started_at for sub in friend.subscriptions):
        return None
    friend.referred_by_id = referrer.id if referrer else None
    referral = Referral(referrer_id=referrer.id if referrer else None, friend_id=friend.id,
                        code_id=rc.id if rc else None)
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


def friend_offer(db: Session, friend: User) -> tuple[int, int]:
    """(percent off, months) for this referred family."""
    referral = _referral(db, friend)
    rc = db.get(ReferralCode, referral.code_id) if referral and referral.code_id else None
    return (rc.percent_off, rc.months) if rc else (REFERRAL_FRIEND_PERCENT, 1)


def coupon_for(settings: Settings, db: Session, friend: User) -> str:
    """The Stripe coupon for this family's discount, created the first time it's used. Coupons are
    named by their terms, so changing a code's offer later makes a new coupon rather than altering one."""
    percent, months = friend_offer(db, friend)
    referral = _referral(db, friend)
    rc = db.get(ReferralCode, referral.code_id) if referral and referral.code_id else None
    stripe.api_key = settings.stripe_secret_key
    cid = f"code-{rc.code}-{percent}-{months}" if rc else f"referral-{percent}-once"
    try:
        stripe.Coupon.retrieve(cid)
    except stripe.InvalidRequestError:
        terms = {"duration": "once"} if months == 1 else {"duration": "repeating", "duration_in_months": months}
        stripe.Coupon.create(id=cid, percent_off=percent, name=(f"{rc.code}: " if rc else "A friend's invite: ")
                             + offer_text(percent, months), **terms)
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
    if not referral.code_id and len(rewarded) >= REFERRAL_MAX_PER_YEAR:  # custom codes: the admin set the terms
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
        if referral.referrer_id is None:  # a custom code with no family to reward: counted, nothing to pay
            referral.status, referral.note = "joined", "custom code, no reward set"
            db.commit()
            continue
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

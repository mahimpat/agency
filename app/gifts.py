"""Pen Pal gift subscriptions.

A giver pays once (3, 6 or 12 months) and gets a printable certificate with a
code. The child's parent redeems the code when signing up: no card needed. The
daily job adds each month's letters, emails the parent before the gift ends,
and then the family can carry on with a normal monthly subscription.
"""

import secrets
import tempfile
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path

import stripe
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from letters.render import GRAIN, _font_faces

from .config import GIFT_MONTH_DAYS, GIFT_OPTIONS, GIFT_VALID_DAYS, Settings
from .emails import send_email
from .models import Gift, Subscription, now
from .storage import get_storage

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to confuse


def new_code(db: Session) -> str:
    while True:
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        code = f"LETTER-{raw[:4]}-{raw[4:]}"
        if not db.scalar(select(Gift).where(Gift.code == code)):
            return code


def normalise_code(code: str) -> str:
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    raw = raw.removeprefix("LETTER")
    return f"LETTER-{raw[:4]}-{raw[4:8]}" if len(raw) == 8 else ""


def months_label(months: int) -> str:
    return f"{months} months"


# --- Buying -------------------------------------------------------------------

def create_gift(db: Session, months: int, buyer_name: str, buyer_email: str, child_first_name: str,
                message: str, recipient_email: str = "", send_on: date | None = None, source: str = "",
                campaign: str = "") -> Gift:
    gift = Gift(code=new_code(db), months=months, price_pence=GIFT_OPTIONS[months],
                buyer_name=buyer_name.strip()[:120], buyer_email=buyer_email.strip().lower()[:320],
                child_first_name=child_first_name.strip()[:60], message=message.strip()[:300],
                recipient_email=recipient_email.strip().lower()[:320] if send_on else "",
                send_on=datetime.combine(send_on, time(0, 0)) if send_on and recipient_email else None,
                source=source, campaign=campaign)
    db.add(gift)
    db.commit()
    return gift


def checkout_url(settings: Settings, db: Session, gift: Gift) -> str:
    if not settings.stripe_secret_key:
        raise RuntimeError("Set STRIPE_SECRET_KEY to take payments.")
    stripe.api_key = settings.stripe_secret_key
    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=gift.buyer_email,
        line_items=[{"quantity": 1, "price_data": {
            "currency": "gbp", "unit_amount": gift.price_pence,
            "product_data": {"name": f"Letters from History gift: {months_label(gift.months)} of Pen Pal from History"},
        }}],
        metadata={"gift_id": str(gift.id)},
        payment_intent_data={"metadata": {"gift_id": str(gift.id)}},
        success_url=f"{settings.base_url}/gift/thanks?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.base_url}/gift?cancelled=1",
    )
    gift.stripe_checkout_id = session.id
    db.commit()
    return session.url


def certificate_token(settings: Settings, gift: Gift) -> str:
    return URLSafeSerializer(settings.secret_key, salt="gift-certificate").dumps(gift.id)


def gift_from_token(settings: Settings, db: Session, token: str) -> Gift | None:
    try:
        return db.get(Gift, URLSafeSerializer(settings.secret_key, salt="gift-certificate").loads(token))
    except BadSignature:
        return None


def mark_paid(settings: Settings, db: Session, gift: Gift, payment_intent: str | None, to_pdf) -> bool:
    """Idempotent: Stripe's webhook and the thank-you page may both report the same payment."""
    if gift.status != "pending":
        return False
    gift.status = "paid"
    gift.paid_at = now()
    gift.expires_at = now() + timedelta(days=GIFT_VALID_DAYS)
    gift.stripe_payment_intent = payment_intent
    db.commit()
    make_certificate(settings, db, gift, to_pdf)
    link = f"{settings.base_url}/gift/certificate/{certificate_token(settings, gift)}"
    send_email(settings, gift.buyer_email, "Your Letters from History gift certificate",
               f"Hello {gift.buyer_name},\n\nThank you for giving Letters from History"
               f"{' to ' + gift.child_first_name if gift.child_first_name else ''}!\n\n"
               f"Your printable gift certificate: {link}\n\nGift code: {gift.code}\n"
               f"{months_label(gift.months)} of Pen Pal from History. Redeem by {gift.expires_at:%d %B %Y} at "
               f"{settings.base_url}/redeem\n\n"
               + (f"We'll email the gift to {gift.recipient_email} on {gift.send_on:%A %-d %B}."
                  " You can print the certificate too, if you'd like to give it in a card.\n\n"
                  if gift.send_on else
                  "Print the certificate for a card, or forward this email to the child's parent. They'll add the "
                  "child's details when they redeem it; no card is needed.\n\n")
               + "Letters from History")
    if gift.send_on and gift.send_on <= now():
        send_to_recipient(settings, db, gift)  # chosen day is today: no need to wait for the daily job
    return True


def send_due_to_recipients(settings: Settings, db: Session) -> int:
    """Email gifts to the families on the day the buyer chose. The address is cleared once sent."""
    due = db.scalars(select(Gift).where(Gift.status == "paid", Gift.send_on.is_not(None),
                                        Gift.sent_to_recipient_at.is_(None), Gift.send_on <= now())).all()
    return sum(send_to_recipient(settings, db, gift) for gift in due)


def send_to_recipient(settings: Settings, db: Session, gift: Gift) -> bool:
    """Send one scheduled gift email. The gift is claimed first (one atomic update), so the daily job
    and a payment arriving at the same moment can't both send it. If sending fails, the claim is
    released and the address kept, so the next daily run tries again."""
    if not gift.recipient_email:
        return False
    claimed = db.execute(update(Gift).where(Gift.id == gift.id, Gift.sent_to_recipient_at.is_(None))
                         .values(sent_to_recipient_at=now())).rowcount
    db.commit()
    if not claimed:
        return False
    db.refresh(gift)
    link = f"{settings.base_url}/gift/certificate/{certificate_token(settings, gift)}"
    who = gift.child_first_name or "your child"
    ok = send_email(settings, gift.recipient_email, f"A gift for {who} from {gift.buyer_name}",
                    f"Hello,\n\n{gift.buyer_name} has given {who} {months_label(gift.months)} of Letters from History: "
                    "real letters through the letterbox from history's great minds, written just for them. When "
                    "they write back, the next letter answers.\n\n"
                    + (f"Their message: \"{gift.message}\"\n\n" if gift.message else "")
                    + f"The gift certificate (print it if you like): {link}\n\nGift code: {gift.code}\n"
                    f"To start, a parent or guardian redeems the code at {settings.base_url}/redeem and adds the child's "
                    f"details and a UK address. No card is needed. Please redeem by {gift.expires_at:%d %B %Y}.\n\n"
                    "Letters from History")
    if ok:
        gift.recipient_email = ""
    else:
        gift.sent_to_recipient_at = None
    db.commit()
    return ok


def confirm_from_stripe(settings: Settings, db: Session, session_id: str, to_pdf) -> Gift | None:
    """Thank-you page: check the payment directly with Stripe rather than waiting for the webhook."""
    gift = db.scalar(select(Gift).where(Gift.stripe_checkout_id == session_id))
    if not gift:
        return None
    if gift.status == "pending" and settings.stripe_secret_key:
        stripe.api_key = settings.stripe_secret_key
        session = stripe.checkout.Session.retrieve(session_id).to_dict()
        if session.get("payment_status") == "paid":
            mark_paid(settings, db, gift, session.get("payment_intent"), to_pdf)
    return gift


# --- Certificate -----------------------------------------------------------------

CERT_STYLE = """
@page { size: A4; margin: 0; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; }
.cert { width: 210mm; height: 297mm; box-sizing: border-box; padding: 30mm 26mm; position: relative; text-align: center;
  color: #2b1f14; font-family: 'Fell', Georgia, serif; background: url("GRAIN"),
  repeating-linear-gradient(0deg, rgba(120,100,70,.045) 0 1px, transparent 1px 3px), #f5edd9; }
.cert::before, .cert::after { content: ""; position: absolute; left: 0; right: 0; height: 5mm;
  background: repeating-linear-gradient(135deg, #b8322b 0 6mm, transparent 6mm 9mm, #1f3f7a 9mm 15mm, transparent 15mm 18mm); }
.cert::before { top: 12mm; } .cert::after { bottom: 12mm; }
.small { font-size: 11pt; letter-spacing: .25em; text-transform: uppercase; color: #6d5330; }
h1 { font-weight: 400; font-size: 44pt; margin: 6mm 0 2mm; }
.for { font-family: 'Signature', cursive; font-size: 34pt; margin: 8mm 0 4mm; }
.what { font-size: 15pt; line-height: 1.5; max-width: 140mm; margin: 0 auto 10mm; }
.note { font-family: 'Hand', cursive; font-size: 19pt; color: #1e2a55; max-width: 140mm; margin: 0 auto 4mm; line-height: 1.3; }
.from { font-family: 'Hand', cursive; font-size: 17pt; color: #1e2a55; margin-bottom: 12mm; }
.code { display: inline-block; font-family: 'Typewriter', monospace; font-size: 24pt; letter-spacing: .08em;
  border: 1.2pt solid #2b1f14; padding: 4mm 8mm; background: #fffdf6; margin: 2mm 0 6mm; }
.how { font-size: 12pt; line-height: 1.6; }
.foot { position: absolute; left: 0; right: 0; bottom: 24mm; font-size: 9.5pt; color: #6d5330; }
"""


def certificate_html(settings: Settings, gift: Gift) -> str:
    who = escape(gift.child_first_name) if gift.child_first_name else "someone special"
    note = f'<div class="note">{escape(gift.message)}</div>' if gift.message else ""
    redeem = escape(settings.base_url.replace("https://", "").replace("http://", "")) + "/redeem"
    return f"""<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>Gift certificate</title>
<style>{_font_faces()}{CERT_STYLE.replace("GRAIN", GRAIN)}</style></head><body><div class="cert">
<div class="small">A gift of</div>
<h1>Letters from History</h1>
<div class="for">for {who}</div>
<div class="what">{months_label(gift.months)} of real letters through the letterbox from history's great minds,
written just for you. And when you write back, they answer.</div>
{note}
<div class="from">With love from {escape(gift.buyer_name)}</div>
<div class="small">Your gift code</div>
<div class="code">{escape(gift.code)}</div>
<div class="how">To start, a grown-up visits <b>{redeem}</b> and enters this code.<br>
Please redeem by {gift.expires_at:%d %B %Y}.</div>
<div class="foot">Letters from History · a product of Forem Innovations</div>
</div></body></html>"""


def make_certificate(settings: Settings, db: Session, gift: Gift, to_pdf) -> None:
    html = certificate_html(settings, gift)
    key = f"gifts/{gift.id}/certificate.pdf"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "certificate.pdf"
            to_pdf(html, path)
            get_storage(settings).put(key, path.read_bytes(), "application/pdf")
    except Exception:  # noqa: BLE001 - no PDF maker: keep a printable HTML version instead
        key = f"gifts/{gift.id}/certificate.html"
        get_storage(settings).put(key, html.encode("utf-8"), "text/html")
    gift.certificate_path = key
    db.commit()


# --- Redeeming --------------------------------------------------------------------

def find_redeemable(db: Session, code: str) -> tuple[Gift | None, str]:
    """Returns (gift, "") if the code can be used, or (None, reason for the parent)."""
    gift = db.scalar(select(Gift).where(Gift.code == normalise_code(code))) if normalise_code(code) else None
    if not gift or gift.status in ("pending", "refunded"):
        return None, "We couldn't find that gift code. Please check it and try again."
    if gift.status == "redeemed":
        return None, "That gift code has already been used."
    if gift.status == "expired" or (gift.expires_at and gift.expires_at < now()):
        return None, "That gift code has expired. Please contact us and we'll do our best to help."
    return gift, ""


def redeem(db: Session, gift: Gift, sub: Subscription, user_id: int) -> None:
    """Turn a new child's subscription into an active gift: first month's letters straight away."""
    from .billing import grant_credits
    gift.status = "redeemed"
    gift.redeemed_at = now()
    gift.redeemed_by_user_id = user_id
    sub.plan = "penpal"
    sub.status = "active"
    sub.gift_id = gift.id
    sub.gift_months_left = gift.months - 1
    sub.next_credit_at = now() + timedelta(days=GIFT_MONTH_DAYS)
    grant_credits(sub)
    db.commit()


# --- The daily job ---------------------------------------------------------------

def run_gift_months(db: Session, settings: Settings) -> None:
    """Send gifts scheduled for today; add each gift month's letters; email before the last month;
    end gifts that are used up; expire codes that were never redeemed."""
    from .billing import grant_credits
    send_due_to_recipients(settings, db)
    subs = db.scalars(select(Subscription).where(Subscription.gift_id.is_not(None),
                                                 Subscription.stripe_subscription_id.is_(None),
                                                 Subscription.status == "active",
                                                 Subscription.next_credit_at <= now())).all()
    for sub in subs:
        child, user = sub.child, sub.user
        if sub.gift_months_left > 0:
            grant_credits(sub)
            sub.gift_months_left -= 1
            sub.next_credit_at = now() + timedelta(days=GIFT_MONTH_DAYS)
            db.commit()
            if sub.gift_months_left == 0:
                send_email(settings, user.email, f"{child.first_name}'s gift of letters has one month left",
                           f"Hello {user.name},\n\n{child.first_name}'s gift of Letters from History has one month "
                           "left. If you'd like the letters to carry on, you can continue with Pen Pal from History "
                           f"for £12 a month (cancel any time):\n{settings.base_url}/checkout/{sub.id}\n\n"
                           "If not, there's nothing to do: the gift simply ends.\n\nLetters from History")
        else:
            sub.status = "gift_ended"
            db.commit()
            send_email(settings, user.email, f"{child.first_name}'s gift of letters has ended",
                       f"Hello {user.name},\n\nWe hope {child.first_name} loved their letters! The gift has now ended. "
                       f"To keep the letters coming: {settings.base_url}/checkout/{sub.id}\n\nLetters from History")
    for gift in db.scalars(select(Gift).where(Gift.status == "paid", Gift.expires_at < now())).all():
        gift.status = "expired"
    # A scheduled email that will never be sent (redeemed early, refunded, expired): drop the address now.
    for gift in db.scalars(select(Gift).where(Gift.status != "paid", Gift.recipient_email != "")).all():
        gift.recipient_email = ""
    db.commit()
    forget_old_gifts(db, settings)


def forget_old_gifts(db: Session, settings: Settings) -> None:
    """Privacy: 12 months after a gift is used up, expires or is refunded, clear the buyer's details,
    the child's name and message, and the certificate. Payment records stay with Stripe."""
    keep = timedelta(days=365)
    for gift in db.scalars(select(Gift).where(Gift.status.in_(["redeemed", "expired", "refunded"]),
                                              Gift.buyer_email != "")).all():
        if gift.status == "redeemed" and gift.redeemed_at:
            finished = gift.redeemed_at + timedelta(days=GIFT_MONTH_DAYS * gift.months)
        else:
            finished = gift.expires_at or gift.created_at
        if finished + keep > now():
            continue
        if gift.certificate_path:
            get_storage(settings).delete(gift.certificate_path)
            gift.certificate_path = None
        gift.buyer_name, gift.buyer_email, gift.child_first_name, gift.message = "", "", "", ""
        gift.recipient_email = ""
    db.commit()


def handle_refund(db: Session, payment_intent: str | None) -> str:
    gift = db.scalar(select(Gift).where(Gift.stripe_payment_intent == payment_intent)) if payment_intent else None
    if not gift:
        return "no gift"
    if gift.status == "paid":
        gift.status = "refunded"  # the code can no longer be redeemed
        db.commit()
        return f"gift {gift.id} refunded"
    return f"gift {gift.id} refunded after redemption: review manually"


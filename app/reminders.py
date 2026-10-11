"""Subscription reminders, as the UK subscription rules (DMCC Act 2024, Part 4 Chapter 2) expect.

A monthly subscription gets a reminder every 6 months: what it costs, what it includes, that it carries
on until cancelled, and how to cancel in one click. Check the final regulations and guidance when they're
published (expected before the rules start) and adjust REMINDER_EVERY_DAYS or the wording if needed.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import PLANS, Settings
from .emails import send_email
from .models import Letter, Subscription, now

REMINDER_EVERY_DAYS = 182


def run_reminders(db: Session, settings: Settings) -> int:
    due_before = now() - timedelta(days=REMINDER_EVERY_DAYS)
    subs = db.scalars(select(Subscription).where(
        Subscription.stripe_subscription_id.is_not(None), Subscription.status.in_(["active", "trialing"]),
        Subscription.cancel_at.is_(None), Subscription.started_at.is_not(None))).all()
    sent = 0
    for sub in subs:
        if (sub.last_reminder_at or sub.started_at) > due_before:
            continue
        plan, child, user = PLANS[sub.plan], sub.child, sub.user
        posted = db.query(Letter).filter(Letter.child_id == child.id, Letter.status == "sent").count()
        send_email(settings, user.email, f"A reminder about {child.first_name}'s Letters from History subscription",
                   f"Hello {user.name},\n\nThis is our regular reminder that {child.first_name} has a "
                   f"{plan['name']} subscription. It costs {plan['price_label']} and carries on each month until you "
                   f"cancel. So far we've posted {posted} letter{'s' if posted != 1 else ''} to {child.first_name}.\n\n"
                   "Each month includes: "
                   + ("two letters, and when your child writes back, the next letter answers them."
                      if plan["reply"] else "one personalised letter.")
                   + f"\n\nTo cancel, go to {settings.base_url}/account and choose \"Cancel subscription\". It takes "
                   "one click, and letters stop at the end of the month you've paid for. Nothing to do if you'd "
                   "like the letters to continue.\n\nLetters from History")
        sub.last_reminder_at = now()
        sent += 1
    db.commit()
    return sent

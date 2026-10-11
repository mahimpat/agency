"""Simple rate limiting stored in the database, so it holds across restarts and servers."""

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import RATE_LIMITS
from .models import RateEvent, now


def allow(db: Session, action: str, who: str) -> bool:
    """Record an attempt and return False if `who` has used up `action`'s limit."""
    limit, window = RATE_LIMITS[action]
    key = f"{action}:{who.lower()[:150]}"
    since = now() - timedelta(seconds=window)
    used = db.scalar(select(func.count(RateEvent.id)).where(RateEvent.key == key, RateEvent.created_at >= since))
    if (used or 0) >= limit:
        return False
    db.add(RateEvent(key=key))
    db.commit()
    return True


def prune(db: Session) -> None:
    db.execute(delete(RateEvent).where(RateEvent.created_at < now() - timedelta(days=1)))
    db.commit()

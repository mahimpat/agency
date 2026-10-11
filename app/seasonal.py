"""Christmas: tell families the last day to order for a letter to arrive in time.

The last order day is set by CHRISTMAS_LAST_ORDER (month-day, default 12-10). Check Royal Mail's
published last 2nd class posting date each autumn and allow about 6 working days before it: the
first letter is written, checked, maybe previewed by the parent for up to 2 days, then printed and posted.
"""

import os
from datetime import date

NOTICE_FROM = (11, 1)  # shown from 1 November to Christmas Day


def last_order_day(year: int) -> date:
    month, day = (int(x) for x in os.environ.get("CHRISTMAS_LAST_ORDER", "12-10").split("-"))
    return date(year, month, day)


def christmas_notice(today: date) -> dict | None:
    """What to show on the landing, sign-up and gift pages, or None outside the season."""
    if not date(today.year, *NOTICE_FROM) <= today <= date(today.year, 12, 25):
        return None
    last = last_order_day(today.year)
    if today <= last:
        return {"in_time": True, "last": last,
                "text": f"Christmas: sign up by {last:%A %-d December} for the first letter to arrive before "
                        "Christmas. Gift certificates arrive by email straight away, so you can give one right up "
                        "to Christmas Day."}
    return {"in_time": False, "last": last,
            "text": "Christmas: it's too late for a letter to arrive by post before Christmas, but gift "
                    "certificates arrive by email straight away. You can even choose to send it on Christmas morning."}

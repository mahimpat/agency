"""Connection check: is everything set up correctly? Safe to run any time.

    python -m app.manage check                 # all services, nothing sent or charged
    python -m app.manage check --stannp-proof  # also create a free Stannp TEST proof

Nothing here spends money or posts anything: Claude is checked with a model
lookup (no tokens), Stripe by reading the prices, email by logging in only.
"""

import secrets
import smtplib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from .config import PLANS, Settings
from .models import make_sessionmaker
from .storage import get_storage

EXPECTED_PENCE = {"story": 700, "penpal": 1200}


@dataclass
class Result:
    name: str
    ok: bool | None  # None = optional and not set up
    detail: str


def _settings_check(settings: Settings) -> Result:
    required = {
        "SECRET_KEY": settings.secret_key and settings.secret_key != "dev-only-insecure-key",
        "BASE_URL (https)": settings.base_url.startswith("https://"),
        "STRIPE_SECRET_KEY": settings.stripe_secret_key, "STRIPE_WEBHOOK_SECRET": settings.stripe_webhook_secret,
        "STRIPE_PRICE_STORY": settings.stripe_prices.get("story"),
        "STRIPE_PRICE_PENPAL": settings.stripe_prices.get("penpal"),
        "CRON_SECRET": settings.cron_secret, "ADMIN_EMAIL": settings.admin_email,
        "CONTACT_EMAIL": settings.contact_email and "example.com" not in settings.contact_email,
        "COMPANY_NUMBER": settings.company_number, "COMPANY_ADDRESS": settings.company_address,
        "ICO_NUMBER": settings.ico_number, "SMTP_HOST": settings.smtp_host,
    }
    missing = [name for name, value in required.items() if not value]
    return Result("Settings", not missing, "all production settings present" if not missing
                  else "missing or placeholder: " + ", ".join(missing))


def _database(settings: Settings) -> Result:
    try:
        db = make_sessionmaker(settings.database_url, create_tables=False)()
        db.execute(text("select 1"))
        db.close()
        return Result("Database", True, settings.database_url.split("@")[-1])
    except Exception as e:  # noqa: BLE001
        return Result("Database", False, str(e)[:200])


def _storage(settings: Settings) -> Result:
    store = get_storage(settings)
    key = f"healthcheck/{secrets.token_hex(6)}.txt"
    try:
        store.put(key, b"ok", "text/plain")
        ok = store.get(key) == b"ok"
        store.delete(key)
        where = f"bucket {settings.storage_bucket}" if settings.storage_bucket else f"local {settings.data_dir}"
        return Result("File storage", ok, where)
    except Exception as e:  # noqa: BLE001
        return Result("File storage", False, str(e)[:200])


def _pdf() -> Result:
    from letters import pdf
    if not pdf.find_chrome():
        return Result("PDF maker", False, "Chrome/Chromium not found (set CHROME=/path)")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pages = pdf.html_to_pdf("<html><body><p>check</p></body></html>", Path(tmp) / "check.pdf")
        return Result("PDF maker", pages >= 1, f"{pdf.find_chrome()}")
    except Exception as e:  # noqa: BLE001
        return Result("PDF maker", False, str(e)[:200])


def _claude() -> Result:
    import os
    from letters import llm
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return Result("Claude API", False, "ANTHROPIC_API_KEY not set")
    try:
        model = llm.client().models.retrieve(llm.MODEL)
        return Result("Claude API", True, f"{model.id} available")
    except Exception as e:  # noqa: BLE001 - missing key, bad key, network
        return Result("Claude API", False, f"{type(e).__name__}: {str(e)[:160]}")


def _stripe(settings: Settings) -> Result:
    if not settings.stripe_secret_key:
        return Result("Stripe", False, "STRIPE_SECRET_KEY not set")
    import stripe
    stripe.api_key = settings.stripe_secret_key
    problems, mode = [], "live" if settings.stripe_secret_key.startswith("sk_live") else "test"
    for plan, price_id in settings.stripe_prices.items():
        if not price_id:
            problems.append(f"{plan}: no price ID")
            continue
        try:
            price = stripe.Price.retrieve(price_id).to_dict()
        except Exception as e:  # noqa: BLE001
            problems.append(f"{plan}: {str(e)[:80]}")
            continue
        recurring = price.get("recurring") or {}
        if price.get("currency") != "gbp" or recurring.get("interval") != "month" or \
                price.get("unit_amount") != EXPECTED_PENCE[plan]:
            problems.append(f"{plan}: expected £{EXPECTED_PENCE[plan] / 100:.0f}/month GBP, got "
                            f"{price.get('unit_amount')} {price.get('currency')} per {recurring.get('interval')}")
    if not settings.stripe_webhook_secret:
        problems.append("STRIPE_WEBHOOK_SECRET not set")
    return Result("Stripe", not problems, f"{mode} mode, prices OK" if not problems else "; ".join(problems))


def _stannp(settings: Settings, proof: bool) -> Result:
    import os
    from letters import mail
    if not os.environ.get("STANNP_API_KEY"):
        return Result("Stannp", False, "STANNP_API_KEY not set")
    if not proof:
        return Result("Stannp", True, "key set (run with --stannp-proof to create a free test proof)")
    sample = Path(__file__).resolve().parent.parent / "samples" / "anning-to-oliver.pdf"
    to = {"name": "Proof Check", "line1": "1 Example Street", "town": "Bristol", "postcode": "BS1 4ST", "country": "GB"}
    try:
        result = mail.send_stannp(sample, to, live=False)
        return Result("Stannp", True, f"test proof created: {result.get('proof')} (cost if live: {result.get('cost')})")
    except Exception as e:  # noqa: BLE001
        return Result("Stannp", False, str(e)[:200])


def _email(settings: Settings) -> Result:
    if not settings.smtp_host:
        return Result("Email", False, "SMTP_HOST not set (emails only go to the log)")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
        return Result("Email", True, f"logged in to {settings.smtp_host} (nothing sent)")
    except Exception as e:  # noqa: BLE001
        return Result("Email", False, str(e)[:200])


def _optional(name: str, configured: bool, detail: str) -> Result:
    return Result(name, True if configured else None, detail if configured else "not set up (optional)")


def run_checks(settings: Settings, stannp_proof: bool = False) -> list[Result]:
    return [
        _settings_check(settings), _database(settings), _storage(settings), _pdf(), _claude(),
        _stripe(settings), _stannp(settings, stannp_proof), _email(settings),
        _optional("Sign in with Google", bool(settings.google_client_id and settings.google_client_secret),
                  f"redirect URI: {settings.base_url}/auth/google/callback"),
        _optional("Error alerts (Sentry)", bool(settings.sentry_dsn), "SENTRY_DSN set"),
    ]


def print_report(results: list[Result]) -> bool:
    marks = {True: "OK  ", False: "FAIL", None: "--  "}
    for r in results:
        print(f"[{marks[r.ok]}] {r.name:<22} {r.detail}")
    failed = [r.name for r in results if r.ok is False]
    print("\nAll required checks passed." if not failed else f"\n{len(failed)} to fix: {', '.join(failed)}")
    return not failed


__all__ = ["run_checks", "print_report", "PLANS"]

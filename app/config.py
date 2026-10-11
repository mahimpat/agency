"""Settings, read from environment variables (see .env.example)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

COMPANY = "Forem Innovations"
PRODUCT = "Letters from History"

# Letters each paid billing period buys. Pen Pal's second letter answers the
# child's reply, or becomes a follow-up story letter if no reply comes in time.
PLANS = {
    "story": {"name": "Story Letters", "price_label": "£7 a month", "story": 1, "reply": 0,
              "price_env": "STRIPE_PRICE_STORY"},
    "penpal": {"name": "Pen Pal from History", "price_label": "£12 a month", "story": 1, "reply": 1,
               "price_env": "STRIPE_PRICE_PENPAL"},
}

# Each child spends a few months with one pen pal, then meets the next.
FIGURE_ORDER = ["einstein", "amelia-earhart", "leonardo-da-vinci", "cleopatra"]
LETTERS_PER_FIGURE = 3
REPLY_WAIT_DAYS = 21  # after this, an unused reply credit becomes a follow-up letter


def _flag(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    base_url: str = field(default_factory=lambda: os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/"))
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", ""))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "data")))
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    stripe_secret_key: str = field(default_factory=lambda: os.environ.get("STRIPE_SECRET_KEY", ""))
    stripe_webhook_secret: str = field(default_factory=lambda: os.environ.get("STRIPE_WEBHOOK_SECRET", ""))
    stripe_prices: dict = field(default_factory=lambda: {
        plan: os.environ.get(cfg["price_env"], "") for plan, cfg in PLANS.items()})
    # Human approval for every letter (recommended at launch). False = only flagged letters wait.
    review_all: bool = field(default_factory=lambda: _flag("REVIEW_ALL", True))
    # False = Stannp test proofs only. True = letters are really printed and posted.
    live_mail: bool = field(default_factory=lambda: _flag("LIVE_MAIL", False))
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.environ.get("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD", ""))
    mail_from: str = field(default_factory=lambda: os.environ.get("MAIL_FROM", "Letters from History <hello@example.com>"))
    admin_email: str = field(default_factory=lambda: os.environ.get("ADMIN_EMAIL", ""))
    contact_email: str = field(default_factory=lambda: os.environ.get("CONTACT_EMAIL", "privacy@example.com"))
    # Shown in the privacy policy and terms. Fill these in before launch.
    company_legal_name: str = field(default_factory=lambda: os.environ.get("COMPANY_LEGAL_NAME", "Forem Innovations"))
    company_number: str = field(default_factory=lambda: os.environ.get("COMPANY_NUMBER", ""))
    company_address: str = field(default_factory=lambda: os.environ.get("COMPANY_ADDRESS", ""))
    ico_number: str = field(default_factory=lambda: os.environ.get("ICO_NUMBER", ""))
    # A scheduler calls POST /internal/run-due with "Authorization: Bearer <CRON_SECRET>" once a day.
    cron_secret: str = field(default_factory=lambda: os.environ.get("CRON_SECRET", ""))
    hosting_provider: str = field(default_factory=lambda: os.environ.get("HOSTING_PROVIDER", ""))
    email_provider: str = field(default_factory=lambda: os.environ.get("EMAIL_PROVIDER", ""))

    def __post_init__(self):
        if not self.secret_key:
            if self.base_url.startswith("https://"):
                raise RuntimeError("Set SECRET_KEY to a long random string in production.")
            self.secret_key = "dev-only-insecure-key"
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'app.db'}"
        for sub in ("uploads", "letters"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def letters_dir(self) -> Path:
        return self.data_dir / "letters"

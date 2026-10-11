"""Settings, read from environment variables (see .env.example)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

COMPANY = "Forem Innovations"
PRODUCT = "Letters from History"

# Letters each paid billing period buys. Pen Pal's second letter answers the
# child's reply, or becomes a follow-up story letter if no reply comes in time.
PLANS = {
    "story": {"name": "Story Letters", "price_label": "£7 a month", "pence": 700, "story": 1, "reply": 0,
              "price_env": "STRIPE_PRICE_STORY"},
    "penpal": {"name": "Pen Pal from History", "price_label": "£12 a month", "pence": 1200, "story": 1, "reply": 1,
               "price_env": "STRIPE_PRICE_PENPAL"},
}

# Pen Pal gifts: months -> price in pence. Paid once; no renewal. Redeem within GIFT_VALID_DAYS.
GIFT_OPTIONS = {3: 3600, 6: 6900, 12: 13200}
GIFT_VALID_DAYS = 365
GIFT_MONTH_DAYS = 30  # each gift month adds a month of Pen Pal letters
GIFT_SEND_AHEAD_DAYS = 120  # buyers can schedule the gift email up to this far ahead

# Each child spends a few months with one pen pal, then meets the next.
# Starts with UK curriculum favourites; the first pen pal is a fossil hunter, because dinosaurs.
# Mixed so that science, engineering, exploration, leaders and the arts take turns.
FIGURE_ORDER = [
    "mary-anning", "isambard-kingdom-brunel", "ada-lovelace", "grace-darling", "neil-armstrong",
    "florence-nightingale", "samuel-pepys", "charles-darwin", "elizabeth-i", "george-stephenson", "mary-seacole",
    "isaac-newton", "william-shakespeare", "walter-tull", "al-khwarizmi", "emmeline-pankhurst",
    "einstein", "amelia-earhart", "leonardo-da-vinci", "cleopatra",
]
LETTERS_PER_FIGURE = 3
REPLY_WAIT_DAYS = 21  # after this, an unused reply credit becomes a follow-up letter
# Data minimisation: letter text, files and children's reply words are cleared after this many days.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "365"))
PREVIEW_HOURS = 48  # parents who preview letters have this long before a letter posts automatically
# Cost guards: every rewrite or uploaded reply means paid AI calls, so both are capped per child.
# (limit, window in seconds) for each action, per IP address and per account/email.
RATE_LIMITS = {
    "login_ip": (20, 15 * 60), "login_email": (10, 15 * 60),
    "signup_ip": (5, 60 * 60), "forgot_ip": (5, 60 * 60), "forgot_email": (3, 60 * 60),
    "verify_resend": (3, 60 * 60), "redeem_ip": (10, 60 * 60), "gift_ip": (10, 60 * 60), "upload_user": (10, 60 * 60), "changes_user": (10, 60 * 60),
}
PARENT_REWRITES_PER_MONTH = int(os.environ.get("PARENT_REWRITES_PER_MONTH", "2"))
REPLY_UPLOADS_PER_MONTH = int(os.environ.get("REPLY_UPLOADS_PER_MONTH", "4"))


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
    # Files: local DATA_DIR unless an S3-compatible bucket is set (Supabase Storage, Cloudflare R2, AWS S3).
    storage_bucket: str = field(default_factory=lambda: os.environ.get("STORAGE_BUCKET", ""))
    storage_endpoint: str = field(default_factory=lambda: os.environ.get("STORAGE_ENDPOINT", ""))
    storage_region: str = field(default_factory=lambda: os.environ.get("STORAGE_REGION", ""))
    storage_access_key: str = field(default_factory=lambda: os.environ.get("STORAGE_ACCESS_KEY", ""))
    storage_secret_key: str = field(default_factory=lambda: os.environ.get("STORAGE_SECRET_KEY", ""))
    # "Sign in with Google" (optional): OAuth client from Google Cloud Console.
    google_client_id: str = field(default_factory=lambda: os.environ.get("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: os.environ.get("GOOGLE_CLIENT_SECRET", ""))
    # Error alerts (optional). Never sends request bodies or local variables, so no children's data.
    sentry_dsn: str = field(default_factory=lambda: os.environ.get("SENTRY_DSN", ""))
    # Brute-force and bot protection on login, sign-up, password reset and uploads.
    rate_limits: bool = field(default_factory=lambda: _flag("RATE_LIMITS", True))
    # Welcome flyer for the grown-ups, posted with each family's first letter (one extra sheet).
    welcome_flyer: bool = field(default_factory=lambda: _flag("WELCOME_FLYER", True))
    # Founding-family wording on the flyer ("you're one of our first families"). Turn off once you've grown.
    flyer_founding: bool = field(default_factory=lambda: _flag("FLYER_FOUNDING", True))
    # Create tables at start-up. On for SQLite (development); production Postgres uses Alembic migrations.
    auto_create_tables: bool | None = field(default_factory=lambda: (
        _flag("AUTO_CREATE_TABLES", False) if "AUTO_CREATE_TABLES" in os.environ else None))
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
        if self.auto_create_tables is None:
            self.auto_create_tables = self.database_url.startswith("sqlite")
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

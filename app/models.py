"""Database tables. SQLite by default; set DATABASE_URL for Postgres."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(300))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Google account ID for "Sign in with Google" (the stable "sub" claim, not the email).
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # Which link brought them (first visit): ?ref= or utm_source, the campaign, and the page they landed on.
    source: Mapped[str] = mapped_column(String(100), default="", index=True)
    campaign: Mapped[str] = mapped_column(String(200), default="")
    landing: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    children: Mapped[list["Child"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Child(Base):
    __tablename__ = "children"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    first_name: Mapped[str] = mapped_column(String(60))
    age: Mapped[int] = mapped_column(Integer)
    interests: Mapped[str] = mapped_column(Text, default="")
    avoid: Mapped[str] = mapped_column(Text, default="")
    country: Mapped[str] = mapped_column(String(2), default="GB")
    # Where letters are posted
    recipient_name: Mapped[str] = mapped_column(String(120))
    line1: Mapped[str] = mapped_column(String(200))
    line2: Mapped[str] = mapped_column(String(200), default="")
    town: Mapped[str] = mapped_column(String(100))
    postcode: Mapped[str] = mapped_column(String(12))
    # Parent preview: read each letter (after our checks) before it's posted.
    parent_preview: Mapped[bool] = mapped_column(Boolean, default=False)
    # The parent's request for the next letter, after they asked for a rewrite.
    next_letter_note: Mapped[str] = mapped_column(Text, default="")
    # Paused by our team (e.g. while a safeguarding concern is handled): no letters are written or posted.
    paused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pause_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    user: Mapped[User] = relationship(back_populates="children")
    subscription: Mapped["Subscription | None"] = relationship(back_populates="child", uselist=False)
    letters: Mapped[list["Letter"]] = relationship(back_populates="child", cascade="all, delete-orphan",
                                                  order_by="Letter.created_at.desc()")
    replies: Mapped[list["Reply"]] = relationship(back_populates="child", cascade="all, delete-orphan",
                                                 order_by="Reply.created_at.desc()")

    def address(self) -> dict:
        return {"name": self.recipient_name, "line1": self.line1, "line2": self.line2,
                "town": self.town, "postcode": self.postcode, "country": self.country}

    def profile(self) -> dict:
        """What the letter writer is told about the child (no surname, no address)."""
        return {"name": self.first_name, "age": self.age, "country": self.country,
                "interests": [x.strip() for x in self.interests.split(",") if x.strip()],
                "avoid": [x.strip() for x in self.avoid.split(",") if x.strip()]}


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), unique=True)
    plan: Mapped[str] = mapped_column(String(20))
    # pending (checkout not finished), active, past_due, canceled, ...
    status: Mapped[str] = mapped_column(String(30), default="pending")
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    story_credits: Mapped[int] = mapped_column(Integer, default=0)
    reply_credits: Mapped[int] = mapped_column(Integer, default=0)
    reply_credit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Gift subscriptions: paid once by someone else; the daily job adds each month's letters.
    gift_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gift_months_left: Mapped[int] = mapped_column(Integer, default=0)
    next_credit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Subscription rules (DMCC Act): when paid billing started, a cancellation that takes effect at
    # the end of the paid month, and when we last sent the "your subscription continues" reminder.
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    user: Mapped[User] = relationship(back_populates="subscriptions")
    child: Mapped[Child] = relationship(back_populates="subscription")


class Reply(Base):
    """A child's letter back. The photo is deleted as soon as it has been read."""
    __tablename__ = "replies"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    figure: Mapped[str] = mapped_column(String(40))
    typed_text: Mapped[str] = mapped_column(Text, default="")
    photo_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    transcription_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # new -> ready (read, waiting for a letter) -> used; or held (needs a human) / dismissed
    status: Mapped[str] = mapped_column(String(20), default="new")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    child: Mapped[Child] = relationship(back_populates="replies")


class Letter(Base):
    __tablename__ = "letters"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    figure: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))  # story, reply, follow_up
    # needs_review, held, approved, awaiting_parent, sent, rejected, parent_rewrite, failed
    status: Mapped[str] = mapped_column(String(20), index=True)
    preview_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    letter_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    checks_json: Mapped[str] = mapped_column(Text, default="[]")
    note: Mapped[str] = mapped_column(Text, default="")
    pdf_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    html_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reply_id: Mapped[int | None] = mapped_column(ForeignKey("replies.id"), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    proof_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cost: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resent_count: Mapped[int] = mapped_column(Integer, default=0)
    child: Mapped[Child] = relationship(back_populates="letters")


class Gift(Base):
    """A Pen Pal gift: paid once by the giver, redeemed by the child's parent with the code."""
    __tablename__ = "gifts"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    months: Mapped[int] = mapped_column(Integer)
    price_pence: Mapped[int] = mapped_column(Integer)
    buyer_name: Mapped[str] = mapped_column(String(120))
    buyer_email: Mapped[str] = mapped_column(String(320))
    child_first_name: Mapped[str] = mapped_column(String(60), default="")  # optional, for the certificate
    message: Mapped[str] = mapped_column(Text, default="")
    # pending (at checkout), paid, redeemed, expired, refunded
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    stripe_checkout_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    stripe_payment_intent: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    certificate_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    redeemed_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Optional: email the certificate to the family on a chosen day (e.g. Christmas morning).
    # The address is cleared once it's sent.
    recipient_email: Mapped[str] = mapped_column(String(320), default="")
    send_on: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_to_recipient_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(100), default="", index=True)
    campaign: Mapped[str] = mapped_column(String(200), default="")


class SafeguardingRecord(Base):
    """What a person saw and did about a child's letter. Kept as long as needed to protect the
    child, so it doesn't depend on the account still existing: it stores a snapshot of who."""
    __tablename__ = "safeguarding_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    reply_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject: Mapped[str] = mapped_column(Text)  # e.g. "Maya (9), child #3; parent Priya <p@example.com>"
    action: Mapped[str] = mapped_column(String(30))  # released, dismissed, paused, resumed, note
    notes: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ProcessedEvent(Base):
    """Stripe webhook events already handled (Stripe can deliver an event more than once)."""
    __tablename__ = "processed_events"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RateEvent(Base):
    """One attempt at a rate-limited action (login, sign-up, upload...). Old rows are pruned daily."""
    __tablename__ = "rate_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class JobLock(Base):
    """Stops two daily runs overlapping (e.g. the scheduler and "Run now" at the same moment)."""
    __tablename__ = "job_locks"
    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=now)


def make_sessionmaker(database_url: str, create_tables: bool = True) -> sessionmaker:
    # Hosted Postgres (Supabase, Render, Railway...) often hands out postgres:// URLs.
    if database_url.startswith(("postgres://", "postgresql://")):
        database_url = "postgresql+psycopg://" + database_url.split("://", 1)[1]
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    if create_tables:
        Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)

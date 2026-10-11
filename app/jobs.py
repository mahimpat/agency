"""The daily run: read new replies, write the letters that paid credits allow,
and post approved letters. Safe to run as often as you like.

    python -m app.manage run-due      (schedule it daily, e.g. with cron)

Which pen pal a child has, and what kind of letter comes next, is worked out
from the letters already written, so rejecting a letter never leaves the
rotation in a wrong state.
"""

import io
import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable

from pypdf import PdfReader, PdfWriter
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from letters import llm, mail, pdf, pipeline, render

from .config import (FIGURE_ORDER, LETTERS_PER_FIGURE, PARENT_REWRITES_PER_MONTH, PLANS, PREVIEW_HOURS,
                     REPLY_WAIT_DAYS, RETENTION_DAYS, Settings)
from . import gifts, ratelimit, referrals, reminders
from .emails import send_email
from .models import Child, JobLock, Letter, Reply, SafeguardingRecord, Subscription, User, now
from .flyer import flyer_pdf
from .storage import get_storage

log = logging.getLogger("letters.jobs")

STORY_KINDS = ("intro", "story", "farewell")
DEAD = ("rejected", "parent_rewrite", "failed")


@dataclass
class Services:
    """The outside world, swappable in tests."""
    write_letter: Callable = pipeline.write_letter
    read_reply: Callable = pipeline.read_reply
    to_pdf: Callable = pdf.html_to_pdf
    send: Callable = mail.send_stannp


@dataclass
class RunSummary:
    replies_read: int = 0
    replies_held: int = 0
    letters_written: int = 0
    letters_sent: int = 0
    to_review: int = 0
    sent_to_parents: int = 0
    skipped: bool = False  # another run was already in progress
    errors: list[str] = field(default_factory=list)


# --- Pen pal rotation -------------------------------------------------------

def _live_letters(child: Child) -> list[Letter]:
    return sorted((l for l in child.letters if l.status not in DEAD), key=lambda l: (l.created_at, l.id))


def min_age(figure: str) -> int:
    """A fact sheet can set "## Minimum age" for pen pals whose history needs older readers."""
    match = re.search(r"^## Minimum age\s+(\d+)", llm.load_text("figures", figure), re.MULTILINE)
    return int(match.group(1)) if match else 0


def eligible_figures(age: int) -> list[str]:
    return [f for f in FIGURE_ORDER if age >= min_age(f)]


def _next_figure(previous: str | None, age: int) -> str:
    """The pen pal after `previous` in FIGURE_ORDER that suits this child's age (wrapping round)."""
    eligible = eligible_figures(age)
    if previous not in FIGURE_ORDER:
        return eligible[0]
    start = FIGURE_ORDER.index(previous)
    for step in range(1, len(FIGURE_ORDER) + 1):
        candidate = FIGURE_ORDER[(start + step) % len(FIGURE_ORDER)]
        if candidate in eligible:
            return candidate
    return eligible[0]


def current_figure(child: Child) -> tuple[str, list[Letter]]:
    """The child's current pen pal, and that pen pal's letters in this stint (oldest first).
    A stint starts with an introduction and ends with a farewell; the next pen pal follows in order."""
    letters = _live_letters(child)
    farewell_at = max((i for i, l in enumerate(letters) if l.kind == "farewell"), default=None)
    stint = letters[farewell_at + 1:] if farewell_at is not None else letters
    started = [l for l in stint if l.kind in STORY_KINDS]
    if started:
        figure = started[0].figure
    else:
        figure = _next_figure(letters[farewell_at].figure if farewell_at is not None else None, child.age)
    return figure, [l for l in stint if l.figure == figure]


def next_story_kind(stint: list[Letter]) -> str:
    story_count = sum(1 for l in stint if l.kind in STORY_KINDS)
    if story_count == 0:
        return "intro"
    if story_count >= LETTERS_PER_FIGURE - 1:
        return "farewell"
    return "story"


def history_text(stint: list[Letter], limit: int = 3) -> str:
    parts = []
    for letter in stint[-limit:]:
        if letter.letter_json:
            data = json.loads(letter.letter_json)
            parts.append(f"[{letter.kind} letter]\n{data['greeting']}\n" + "\n".join(data["body"]))
    return "\n\n".join(parts)


# --- Steps ------------------------------------------------------------------

def read_new_replies(db: Session, settings: Settings, child: Child, services: Services, summary: RunSummary) -> None:
    storage = get_storage(settings)
    for reply in [r for r in child.replies if r.status == "new"]:
        try:
            if reply.photo_path:
                with storage.local_path(reply.photo_path) as photo:
                    result = services.read_reply(photo=str(photo))
            else:
                result = services.read_reply(text=reply.typed_text)
            # Always re-apply the rule-based safeguards here, whatever read the letter.
            result = pipeline.apply_safeguards(result)
        except Exception as e:  # noqa: BLE001 - keep the reply and retry on the next run
            summary.errors.append(f"reply {reply.id}: {e}")
            continue
        reply.transcription_json = result.model_dump_json()
        held = result.needs_human or not result.legible
        if reply.photo_path and not held:  # privacy: photos are kept only until they've been read...
            storage.delete(reply.photo_path)  # ...or, if held, until a person has reviewed them
            reply.photo_path = None
        if held:
            reply.status = "held"
            reply.note = result.needs_human_reason or "The letter couldn't be read."
            summary.replies_held += 1
        else:
            reply.status = "ready"
        summary.replies_read += 1
        db.commit()
        if reply.status == "held" and result.legible:
            alert_reviewer(settings, reply, result)


def alert_reviewer(settings: Settings, reply: Reply, result: pipeline.ChildReply) -> None:
    """A held child's letter must be seen by a person quickly, not at the next routine check."""
    if not settings.admin_email:
        log.warning("Reply %s held (%s) but ADMIN_EMAIL is not set", reply.id, result.risk_level)
        return
    urgent = result.risk_level == "urgent"
    subject = ("URGENT: " if urgent else "") + f"A child's letter needs a person to read it (reply {reply.id})"
    send_email(settings, settings.admin_email, subject,
               f"Risk level: {result.risk_level}\nWhy: {reply.note}\n\n"
               f"Read it and follow the safeguarding steps at {settings.base_url}/admin\n"
               + ("\nIf a child may be in immediate danger, call 999.\n" if urgent else ""))


def _make_pdf(settings: Settings, letter: Letter, html: str, services: Services) -> int:
    storage = get_storage(settings)
    key = f"letters/{letter.child_id}/letter-{letter.id}.pdf"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "letter.pdf"
        pages = services.to_pdf(html, path)  # the letter's own pages, checked by the caller
        data = path.read_bytes()
        if _first_letter(letter) and settings.welcome_flyer:
            # The flyer is a nice extra: if it can't be made, the letter still goes, without it.
            try:
                invite = referrals.link_for(settings, object_session(letter), letter.child.user)
                data = with_flyer(data, flyer_pdf(settings, letter.child.subscription.plan, services.to_pdf, invite))
            except Exception:  # noqa: BLE001
                log.exception("Welcome flyer failed for letter %s; posting the letter without it", letter.id)
                letter.note = (letter.note + " Welcome flyer couldn't be made, so it was left out.").strip()
        storage.put(key, data, "application/pdf")
    letter.pdf_path = key
    return pages


def _first_letter(letter: Letter) -> bool:
    """The family's first letter to this child gets the welcome flyer for the grown-ups."""
    if letter.kind != "intro" or letter.child.subscription is None:
        return False
    # Ask the database rather than child.letters: loading that list here, before the new letter is
    # saved, would leave a stale copy that later rotation decisions in this run would read.
    db = object_session(letter)
    earlier = db.scalar(select(func.count()).select_from(Letter).where(
        Letter.child_id == letter.child_id, Letter.status == "sent", Letter.id != letter.id))
    return not earlier


def with_flyer(letter_pdf: bytes, flyer: bytes) -> bytes:
    """The letter's pages, then the flyer sheet: same envelope, letter on top, address in the window."""
    writer = PdfWriter()
    for part in (letter_pdf, flyer):
        writer.append(PdfReader(io.BytesIO(part)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _save_files(settings: Settings, letter: Letter, html: str, services: Services) -> None:
    key = f"letters/{letter.child_id}/letter-{letter.id}.html"
    get_storage(settings).put(key, html.encode("utf-8"), "text/html")
    letter.html_path = key
    pages = _make_pdf(settings, letter, html, services)
    if pages != 2:
        letter.note = (letter.note + f" PDF has {pages} pages (expected 2); check the layout.").strip()


def create_letter(db: Session, settings: Settings, sub: Subscription, kind: str, services: Services,
                  summary: RunSummary, reply: Reply | None = None) -> Letter | None:
    child = sub.child
    figure, stint = current_figure(child)
    if reply:
        figure = reply.figure
        stint = [l for l in _live_letters(child) if l.figure == figure]
    child_reply = pipeline.ChildReply.model_validate_json(reply.transcription_json) if reply else None
    try:
        result = services.write_letter(child.profile(), figure, reply=child_reply,
                                       history=history_text(stint), kind=kind, parent_note=child.next_letter_note)
    except Exception as e:  # noqa: BLE001 - credits are kept, so the next run tries again
        summary.errors.append(f"child {child.id} {kind}: {e}")
        return None

    if result.status == "held" or result.letter is None:
        status = "held"
    elif result.status == "needs_review" or settings.review_all:
        status = "needs_review"
    else:
        status = "approved"
    # child=child (not child_id) also adds it to child.letters, which this session keeps loaded.
    letter = Letter(child=child, figure=figure, kind=kind, status=status, note=result.note,
                    letter_json=result.letter.model_dump_json() if result.letter else None,
                    checks_json=json.dumps([c.model_dump() for c in result.checks]),
                    reply_id=reply.id if reply else None)
    db.add(letter)
    db.flush()
    if result.letter:
        html = render.render_html(result.letter, figure, child.first_name, paper="A4", window=True)
        try:
            _save_files(settings, letter, html, services)
        except Exception as e:  # noqa: BLE001 - the letter is kept; the PDF is retried on approval
            letter.note = (letter.note + f" PDF not made yet: {e}").strip()
            if letter.status == "approved":
                letter.status = "needs_review"
    summary.letters_written += 1
    child.next_letter_note = ""  # the parent's request has been used
    db.commit()
    if letter.status == "approved":
        release_letter(db, settings, letter, services, summary)
    elif letter.status in ("needs_review", "held"):
        summary.to_review += 1
    return letter


def release_letter(db: Session, settings: Settings, letter: Letter, services: Services | None = None,
                   summary: RunSummary | None = None) -> None:
    """A letter has passed our checks. Post it, or first show it to a parent who previews letters."""
    child = letter.child
    if not child.parent_preview:
        send_letter(db, settings, letter, services, summary)
        return
    letter.status = "awaiting_parent"
    letter.preview_deadline = now() + timedelta(hours=PREVIEW_HOURS)
    db.commit()
    if summary:
        summary.sent_to_parents += 1
    name = render.FIGURE_NAMES.get(letter.figure, (letter.figure,))[0]
    send_email(settings, child.user.email, f"Preview {child.first_name}'s letter from {name}",
               f"Hello {child.user.name},\n\n{name} has written to {child.first_name}. You can read the letter "
               f"before it's posted, and approve it or ask for changes:\n{settings.base_url}/letters/{letter.id}/preview\n\n"
               f"If you're happy for it to go, you don't need to do anything: it will be posted automatically in "
               f"{PREVIEW_HOURS // 24} days.\n\nLetters from History")


def parent_rewrites_left(db: Session, child: Child) -> int:
    since = now() - timedelta(days=30)
    used = db.scalar(select(func.count(Letter.id)).where(
        Letter.child_id == child.id, Letter.status == "parent_rewrite", Letter.created_at >= since))
    return max(PARENT_REWRITES_PER_MONTH - (used or 0), 0)


def parent_rewrite(db: Session, letter: Letter, note: str) -> None:
    """The parent asked for changes: drop this letter, return the credit, and pass their note to the writer."""
    reject_letter(db, letter)
    letter.status = "parent_rewrite"
    letter.note = (letter.note + f" Parent asked for changes: {note}").strip()
    letter.child.next_letter_note = note
    db.commit()


def send_letter(db: Session, settings: Settings, letter: Letter, services: Services | None = None,
                summary: RunSummary | None = None) -> None:
    services = services or Services()
    storage = get_storage(settings)
    child = letter.child
    if child.paused_at:
        letter.status = "needs_review"  # back to the queue, ready to approve once letters resume
        letter.note = (letter.note + " Not posted: letters to this child are paused.").strip()
        db.commit()
        if summary:
            summary.errors.append(f"letter {letter.id}: child {child.id} is paused")
        return
    try:
        if not letter.pdf_path or not storage.exists(letter.pdf_path):
            _make_pdf(settings, letter, storage.get(letter.html_path).decode("utf-8"), services)
        with storage.local_path(letter.pdf_path) as pdf_file:
            result = services.send(pdf_file, child.address(), live=settings.live_mail)
    except Exception as e:  # noqa: BLE001
        letter.status = "failed"
        letter.note = (letter.note + f" Sending failed: {e}").strip()
        db.commit()
        if summary:
            summary.errors.append(f"letter {letter.id}: {e}")
        return
    letter.status = "sent"
    letter.provider_id = str(result.get("id") or "")
    letter.proof_url = result.get("proof")
    letter.cost = str(result.get("cost") or "")
    letter.sent_at = now()
    db.commit()
    if summary:
        summary.letters_sent += 1
    if settings.live_mail:
        name = render.FIGURE_NAMES.get(letter.figure, (letter.figure,))[0]
        send_email(settings, child.user.email, f"A letter from {name} is on its way to {child.first_name}",
                   f"Hello {child.user.name},\n\nA letter from {name} has just been posted to {child.first_name}. "
                   "It should arrive in the next few days.\n\nWhen it arrives, you can upload "
                   f"{child.first_name}'s reply at {settings.base_url}/account\n\nLetters from History")


def resend_letter(db: Session, settings: Settings, letter: Letter, services: Services | None = None) -> None:
    """Post a copy of a letter that was lost in the post (the terms promise a free resend)."""
    services = services or Services()
    storage = get_storage(settings)
    if letter.status != "sent" or not letter.pdf_path or not storage.exists(letter.pdf_path):
        raise RuntimeError("Only posted letters whose PDF is still stored can be resent.")
    if letter.child.paused_at:
        raise RuntimeError("Letters to this child are paused.")
    with storage.local_path(letter.pdf_path) as pdf_file:
        result = services.send(pdf_file, letter.child.address(), live=settings.live_mail)
    letter.resent_count += 1
    letter.note = (letter.note + f" Resent {now():%d %b %Y} ({result.get('id')}).").strip()
    db.commit()


def child_subject(child: Child) -> str:
    user = child.user
    return f"{child.first_name} ({child.age}), child #{child.id}; parent {user.name} <{user.email}>"


def record_safeguarding(db: Session, child: Child | None, action: str, notes: str, author: str,
                        reply: Reply | None = None, subject: str | None = None) -> SafeguardingRecord:
    record = SafeguardingRecord(child_id=child.id if child else None, reply_id=reply.id if reply else None,
                                subject=subject or (child_subject(child) if child else "unknown"),
                                action=action, notes=notes.strip()[:5000], author=author)
    db.add(record)
    db.commit()
    return record


def set_paused(db: Session, child: Child, paused: bool, reason: str, author: str) -> None:
    child.paused_at = now() if paused else None
    child.pause_reason = reason.strip()[:1000] if paused else ""
    db.commit()
    record_safeguarding(db, child, "paused" if paused else "resumed", reason or "-", author)


def reject_letter(db: Session, letter: Letter) -> None:
    """Throw a letter away and give the credit back, so the next run writes a fresh one."""
    sub = letter.child.subscription
    letter.status = "rejected"
    if sub:
        if letter.kind in STORY_KINDS:
            sub.story_credits += 1
        else:
            sub.reply_credits += 1
    if letter.reply_id:
        reply = db.get(Reply, letter.reply_id)
        if reply and reply.status == "used":
            reply.status = "ready"
    db.commit()


LOCK_NAME = "run_due"
STALE_LOCK = timedelta(hours=2)  # a crashed run's lock is taken over after this


def _acquire_lock(db: Session) -> bool:
    db.execute(delete(JobLock).where(JobLock.name == LOCK_NAME, JobLock.acquired_at < now() - STALE_LOCK))
    db.commit()
    try:
        db.add(JobLock(name=LOCK_NAME))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _release_lock(db: Session) -> None:
    db.execute(delete(JobLock).where(JobLock.name == LOCK_NAME))
    db.commit()


def run_due(db: Session, settings: Settings, services: Services | None = None) -> RunSummary:
    """Run once at a time: if another run holds the lock, this one does nothing."""
    if not _acquire_lock(db):
        log.warning("run_due skipped: another run is in progress")
        return RunSummary(skipped=True)
    try:
        summary = _run_due(db, settings, services or Services())
    finally:
        _release_lock(db)
    if summary.errors and settings.admin_email:
        send_email(settings, settings.admin_email, f"Daily run finished with {len(summary.errors)} error(s)",
                   "\n".join(summary.errors[:50]) + f"\n\nCheck {settings.base_url}/admin")
    return summary


def apply_retention(db: Session, settings: Settings) -> int:
    """Clear old letters and replies: keep only what the rotation needs (pen pal, type, dates).
    Held replies are left for the safeguarding lead. Returns how many records were cleared."""
    storage = get_storage(settings)
    cutoff = now() - timedelta(days=RETENTION_DAYS)
    cleared = 0
    old_letters = db.scalars(select(Letter).where(Letter.created_at < cutoff, Letter.letter_json.is_not(None),
                                                  Letter.status.not_in(["needs_review", "awaiting_parent", "held"]))).all()
    for letter in old_letters:
        for key in (letter.pdf_path, letter.html_path):
            if key:
                storage.delete(key)
        letter.pdf_path = letter.html_path = letter.letter_json = None
        letter.checks_json = "[]"
        letter.note = ""
        cleared += 1
    old_replies = db.scalars(select(Reply).where(Reply.created_at < cutoff, Reply.status != "held")).all()
    for reply in old_replies:
        if reply.photo_path:
            storage.delete(reply.photo_path)
        if reply.typed_text or reply.transcription_json or reply.photo_path:
            reply.typed_text, reply.transcription_json, reply.photo_path, reply.note = "", None, None, ""
            cleared += 1
    db.commit()
    return cleared


def _run_due(db: Session, settings: Settings, services: Services) -> RunSummary:
    summary = RunSummary()
    ratelimit.prune(db)
    apply_retention(db, settings)
    gifts.run_gift_months(db, settings)  # gift subscriptions get each month's letters here
    referrals.run_rewards(db, settings)  # free months for families whose invited friends stayed
    reminders.run_reminders(db, settings)  # 6-monthly "your subscription continues" emails
    # Letters whose parent preview window has ended are posted, as promised.
    overdue = db.scalars(select(Letter).where(Letter.status == "awaiting_parent", Letter.preview_deadline <= now())).all()
    for letter in overdue:
        if letter.child.paused_at:
            continue  # paused by our team: nothing is posted until we resume
        letter.note = (letter.note + " Posted automatically after the preview window.").strip()
        send_letter(db, settings, letter, services, summary)
    subs = db.scalars(select(Subscription).where(Subscription.status.in_(["active", "trialing"]))).all()
    for sub in subs:
        child = sub.child
        # Replies are always read and safety-checked, even while paused, so nothing is missed.
        read_new_replies(db, settings, child, services, summary)
        if child.paused_at:
            continue

        if sub.story_credits > 0:
            _, stint = current_figure(child)
            if create_letter(db, settings, sub, next_story_kind(stint), services, summary):
                sub.story_credits -= 1
                db.commit()

        if sub.reply_credits > 0 and PLANS[sub.plan]["reply"]:
            ready = sorted((r for r in child.replies if r.status == "ready"), key=lambda r: r.created_at)
            waited = sub.reply_credit_at and now() - sub.reply_credit_at > timedelta(days=REPLY_WAIT_DAYS)
            if ready:
                if create_letter(db, settings, sub, "reply", services, summary, reply=ready[0]):
                    ready[0].status = "used"
                    sub.reply_credits -= 1
                    db.commit()
            elif waited:
                if create_letter(db, settings, sub, "follow_up", services, summary):
                    sub.reply_credits -= 1
                    db.commit()

    if summary.to_review and settings.admin_email:
        send_email(settings, settings.admin_email, f"{summary.to_review} letter(s) waiting for review",
                   f"Review them at {settings.base_url}/admin")
    log.info("run_due: %s", summary)
    return summary

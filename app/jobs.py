"""The daily run: read new replies, write the letters that paid credits allow,
and post approved letters. Safe to run as often as you like.

    python -m app.manage run-due      (schedule it daily, e.g. with cron)

Which pen pal a child has, and what kind of letter comes next, is worked out
from the letters already written, so rejecting a letter never leaves the
rotation in a wrong state.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from letters import mail, pdf, pipeline, render

from .config import (FIGURE_ORDER, LETTERS_PER_FIGURE, PARENT_REWRITES_PER_MONTH, PLANS, PREVIEW_HOURS,
                     REPLY_WAIT_DAYS, Settings)
from .emails import send_email
from .models import Child, Letter, Reply, Subscription, now

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
    errors: list[str] = field(default_factory=list)


# --- Pen pal rotation -------------------------------------------------------

def _live_letters(child: Child) -> list[Letter]:
    return sorted((l for l in child.letters if l.status not in DEAD), key=lambda l: (l.created_at, l.id))


def current_figure(child: Child) -> tuple[str, list[Letter]]:
    """The child's current pen pal, and that pen pal's letters in this stint (oldest first)."""
    letters = _live_letters(child)
    farewells = [i for i, l in enumerate(letters) if l.kind == "farewell"]
    figure = FIGURE_ORDER[len(farewells) % len(FIGURE_ORDER)]
    stint = letters[farewells[-1] + 1:] if farewells else letters
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
    for reply in [r for r in child.replies if r.status == "new"]:
        try:
            if reply.photo_path:
                result = services.read_reply(photo=reply.photo_path)
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
            Path(reply.photo_path).unlink(missing_ok=True)  # ...or, if held, until a person has reviewed them
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


def _save_files(settings: Settings, letter: Letter, html: str, services: Services) -> None:
    folder = settings.letters_dir / str(letter.child_id)
    folder.mkdir(parents=True, exist_ok=True)
    html_path = folder / f"letter-{letter.id}.html"
    html_path.write_text(html, encoding="utf-8")
    letter.html_path = str(html_path)
    pdf_path = folder / f"letter-{letter.id}.pdf"
    pages = services.to_pdf(html, pdf_path)
    letter.pdf_path = str(pdf_path)
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
    letter = Letter(child_id=child.id, figure=figure, kind=kind, status=status, note=result.note,
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
    child = letter.child
    try:
        if not letter.pdf_path or not Path(letter.pdf_path).exists():
            html = Path(letter.html_path).read_text(encoding="utf-8")
            letter.pdf_path = str(Path(letter.html_path).with_suffix(".pdf"))
            services.to_pdf(html, Path(letter.pdf_path))
        result = services.send(Path(letter.pdf_path), child.address(), live=settings.live_mail)
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


def run_due(db: Session, settings: Settings, services: Services | None = None) -> RunSummary:
    services = services or Services()
    summary = RunSummary()
    # Letters whose parent preview window has ended are posted, as promised.
    overdue = db.scalars(select(Letter).where(Letter.status == "awaiting_parent", Letter.preview_deadline <= now())).all()
    for letter in overdue:
        letter.note = (letter.note + " Posted automatically after the preview window.").strip()
        send_letter(db, settings, letter, services, summary)
    subs = db.scalars(select(Subscription).where(Subscription.status.in_(["active", "trialing"]))).all()
    for sub in subs:
        child = sub.child
        read_new_replies(db, settings, child, services, summary)

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

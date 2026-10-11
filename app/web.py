"""The website: landing page, parent accounts, Stripe checkout, reply uploads,
the admin review queue, and the privacy pages.

Run locally:  uvicorn app.web:app --reload
"""

import json
import logging
import secrets
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from letters import render

from . import billing, jobs
from .config import COMPANY, FIGURE_ORDER, PLANS, PREVIEW_HOURS, PRODUCT, REPLY_UPLOADS_PER_MONTH, Settings
from .emails import send_email
from .models import Child, Letter, Reply, Subscription, User, make_sessionmaker
from .models import now as models_now
from .security import (csrf_ok, csrf_token, hash_password, make_reset_token, read_reset_token,
                       verify_password)

log = logging.getLogger("letters.web")
HERE = Path(__file__).parent
MAX_UPLOAD = 10 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
FIGURE_NAMES = {k: v[0] for k, v in render.FIGURE_NAMES.items()}
STATUS_LABELS = {
    "needs_review": "Being checked", "held": "Being checked", "approved": "Ready to post",
    "awaiting_parent": "Waiting for your OK", "parent_rewrite": "Rewritten at your request",
    "sent": "Posted", "rejected": "Rewritten", "failed": "Delayed",
}


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def create_app(settings: Settings | None = None, services: jobs.Services | None = None) -> FastAPI:
    settings = settings or Settings()
    SessionLocal = make_sessionmaker(settings.database_url)
    app = FastAPI(title=PRODUCT, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.SessionLocal = SessionLocal
    app.state.services = services or jobs.Services()
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                       https_only=settings.secure_cookies, max_age=60 * 60 * 24 * 30)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(PREVIEW_HOURS=PREVIEW_HOURS, COMPANY=COMPANY, PRODUCT=PRODUCT, PLANS=PLANS, FIGURE_NAMES=FIGURE_NAMES,
                                 STATUS_LABELS=STATUS_LABELS, contact_email=settings.contact_email,
                                 legal=settings, POLICY_DATE="11 October 2026")

    @app.exception_handler(Redirect)
    async def _redirect(request: Request, exc: Redirect):
        return RedirectResponse(exc.url, status_code=303)

    # --- helpers ------------------------------------------------------------

    def db_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def current_user(request: Request, db: Session) -> User | None:
        uid = request.session.get("uid")
        return db.get(User, uid) if uid else None

    def require_user(request: Request, db: Session = Depends(db_session)) -> User:
        user = current_user(request, db)
        if not user:
            raise Redirect("/login")
        return user

    def require_admin(request: Request, db: Session = Depends(db_session)) -> User:
        user = require_user(request, db)
        if not user.is_admin:
            raise HTTPException(404)
        return user

    async def check_csrf(request: Request) -> None:
        form = await request.form()
        if not csrf_ok(request.session, form.get("csrf")):
            raise HTTPException(400, "Your session expired. Go back, refresh the page and try again.")

    def page(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
        db = SessionLocal()
        try:
            user = current_user(request, db)
            flashes = request.session.pop("flash", [])
            return templates.TemplateResponse(request, template, {
                "user": user, "csrf": csrf_token(request.session), "flashes": flashes, **context},
                status_code=status_code)
        finally:
            db.close()

    def flash(request: Request, message: str) -> None:
        request.session.setdefault("flash", []).append(message)

    def login(request: Request, user: User) -> None:
        request.session.clear()
        request.session["uid"] = user.id

    # --- public pages -------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def landing(request: Request):
        return page(request, "landing.html")

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request):
        return page(request, "privacy.html")

    @app.get("/terms", response_class=HTMLResponse)
    def terms(request: Request):
        return page(request, "terms.html")

    @app.get("/samples/{name}")
    def sample(name: str):
        path = HERE.parent / "site" / "samples" / Path(name).name
        if path.suffix != ".pdf" or not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="application/pdf")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # --- accounts -----------------------------------------------------------

    @app.get("/signup", response_class=HTMLResponse)
    def signup_form(request: Request, plan: str = "penpal"):
        return page(request, "signup.html", plan=plan if plan in PLANS else "penpal")

    @app.post("/signup", dependencies=[Depends(check_csrf)])
    def signup(request: Request, db: Session = Depends(db_session), name: str = Form(...),
               email: str = Form(...), password: str = Form(...), plan: str = Form("penpal"),
               marketing: bool = Form(False), agree: bool = Form(False)):
        email = email.strip().lower()
        errors = []
        if not agree:
            errors.append("Please confirm you've read the privacy policy and terms.")
        if len(password) < 10:
            errors.append("Use a password of at least 10 characters.")
        if "@" not in email or "." not in email.split("@")[-1]:
            errors.append("Enter a valid email address.")
        if db.scalar(select(User).where(User.email == email)):
            errors.append("There's already an account with that email. Try logging in.")
        if errors:
            return page(request, "signup.html", 400, errors=errors, plan=plan, name=name, email=email)
        user = User(email=email, name=name.strip()[:120], password_hash=hash_password(password),
                    marketing_opt_in=marketing)
        db.add(user)
        db.commit()
        login(request, user)
        return RedirectResponse(f"/children/new?plan={plan}", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return page(request, "login.html")

    @app.post("/login", dependencies=[Depends(check_csrf)])
    def login_post(request: Request, db: Session = Depends(db_session), email: str = Form(...),
                   password: str = Form(...)):
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if not user or not verify_password(password, user.password_hash):
            return page(request, "login.html", 400, errors=["That email and password don't match."], email=email)
        login(request, user)
        return RedirectResponse("/admin" if user.is_admin else "/account", status_code=303)

    @app.post("/logout", dependencies=[Depends(check_csrf)])
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/forgot", response_class=HTMLResponse)
    def forgot_form(request: Request):
        return page(request, "forgot.html")

    @app.post("/forgot", dependencies=[Depends(check_csrf)])
    def forgot(request: Request, db: Session = Depends(db_session), email: str = Form(...)):
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user:
            token = make_reset_token(settings.secret_key, user.id, user.password_hash)
            send_email(settings, user.email, "Reset your Letters from History password",
                       f"Hello {user.name},\n\nUse this link within an hour to choose a new password:\n"
                       f"{settings.base_url}/reset/{token}\n\nIf you didn't ask for this, ignore this email.")
        # Same answer either way, so the form can't be used to find out who has an account.
        flash(request, "If that email has an account, we've sent a link to reset the password.")
        return RedirectResponse("/login", status_code=303)

    @app.get("/reset/{token}", response_class=HTMLResponse)
    def reset_form(request: Request, token: str):
        return page(request, "reset.html", token=token)

    @app.post("/reset/{token}", dependencies=[Depends(check_csrf)])
    def reset(request: Request, token: str, db: Session = Depends(db_session), password: str = Form(...)):
        data = read_reset_token(settings.secret_key, token)
        user = db.get(User, data["u"]) if data else None
        if not user or user.password_hash[-12:] != data["h"]:
            return page(request, "reset.html", 400, token=token, errors=["This link has expired. Ask for a new one."])
        if len(password) < 10:
            return page(request, "reset.html", 400, token=token, errors=["Use at least 10 characters."])
        user.password_hash = hash_password(password)
        db.commit()
        login(request, user)
        flash(request, "Your password has been changed.")
        return RedirectResponse("/account", status_code=303)

    # --- parent account -----------------------------------------------------

    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request, user: User = Depends(require_user), checkout: str = ""):
        if checkout == "success":
            flash(request, "Thank you! Your subscription is set up. The first letter will be written in the next "
                           "day or two, checked, and posted.")
        elif checkout == "cancelled":
            flash(request, "Checkout was cancelled. You can finish it any time below.")
        return page(request, "account.html", children=user.children)

    @app.get("/children/new", response_class=HTMLResponse)
    def child_form(request: Request, user: User = Depends(require_user), plan: str = "penpal"):
        return page(request, "child_form.html", plan=plan if plan in PLANS else "penpal", form={})

    @app.post("/children/new", dependencies=[Depends(check_csrf)])
    def child_create(request: Request, user: User = Depends(require_user), db: Session = Depends(db_session),
                     first_name: str = Form(...), age: int = Form(...), interests: str = Form(""),
                     avoid: str = Form(""), recipient_name: str = Form(...), line1: str = Form(...),
                     line2: str = Form(""), town: str = Form(...), postcode: str = Form(...),
                     plan: str = Form("penpal"), guardian: bool = Form(False), parent_preview: bool = Form(False)):
        form = dict(first_name=first_name, age=age, interests=interests, avoid=avoid,
                    recipient_name=recipient_name, line1=line1, line2=line2, town=town, postcode=postcode)
        errors = []
        if not guardian:
            errors.append("Please confirm you're the child's parent or guardian, or have their permission.")
        if not 4 <= age <= 13:
            errors.append("Letters are written for children aged 5 to 12.")
        if plan not in PLANS:
            errors.append("Choose a plan.")
        if errors:
            return page(request, "child_form.html", 400, errors=errors, plan=plan, form=form)
        user = db.merge(user)
        child = Child(user_id=user.id, first_name=first_name.strip()[:60], age=age, interests=interests[:500],
                      avoid=avoid[:500], recipient_name=recipient_name.strip()[:120], line1=line1.strip()[:200],
                      line2=line2.strip()[:200], town=town.strip()[:100], postcode=postcode.strip().upper()[:12],
                      parent_preview=parent_preview)
        db.add(child)
        db.flush()
        sub = Subscription(user_id=user.id, child_id=child.id, plan=plan, status="pending")
        db.add(sub)
        db.commit()
        return RedirectResponse(f"/checkout/{sub.id}", status_code=303)

    @app.get("/checkout/{sub_id}", response_class=HTMLResponse)
    def checkout_page(request: Request, sub_id: int, user: User = Depends(require_user),
                      db: Session = Depends(db_session)):
        sub = db.get(Subscription, sub_id)
        if not sub or sub.user_id != user.id:
            raise HTTPException(404)
        return page(request, "checkout.html", sub=sub)

    @app.post("/checkout/{sub_id}", dependencies=[Depends(check_csrf)])
    def checkout(request: Request, sub_id: int, user: User = Depends(require_user),
                 db: Session = Depends(db_session)):
        sub = db.get(Subscription, sub_id)
        if not sub or sub.user_id != user.id:
            raise HTTPException(404)
        if sub.status not in ("pending", "canceled", "incomplete_expired"):
            flash(request, "That subscription is already active.")
            return RedirectResponse("/account", status_code=303)
        try:
            url = billing.checkout_url(settings, db, db.merge(user), sub)
        except Exception:  # noqa: BLE001
            log.exception("Checkout failed")
            flash(request, "We couldn't start checkout just now. Please try again in a moment.")
            return RedirectResponse(f"/checkout/{sub_id}", status_code=303)
        return RedirectResponse(url, status_code=303)

    @app.post("/billing", dependencies=[Depends(check_csrf)])
    def billing_portal(request: Request, user: User = Depends(require_user), db: Session = Depends(db_session)):
        try:
            return RedirectResponse(billing.portal_url(settings, db, db.merge(user)), status_code=303)
        except Exception:  # noqa: BLE001
            log.exception("Billing portal failed")
            flash(request, "We couldn't open billing just now. Please try again in a moment.")
            return RedirectResponse("/account", status_code=303)

    @app.post("/children/{child_id}/reply", dependencies=[Depends(check_csrf)])
    async def upload_reply(request: Request, child_id: int, user: User = Depends(require_user),
                           db: Session = Depends(db_session)):
        child = db.get(Child, child_id)
        if not child or child.user_id != user.id:
            raise HTTPException(404)
        if not child.subscription or PLANS[child.subscription.plan]["reply"] == 0:
            flash(request, "Replies are part of the Pen Pal from History plan.")
            return RedirectResponse("/account", status_code=303)
        month_ago = models_now() - timedelta(days=30)
        if sum(1 for r in child.replies if r.created_at >= month_ago) >= REPLY_UPLOADS_PER_MONTH:
            flash(request, f"You've sent {REPLY_UPLOADS_PER_MONTH} letters for {child.first_name} this month, which "
                           "is the most we can answer. Please keep this one for next month.")
            return RedirectResponse("/account", status_code=303)
        form = await request.form()
        photo: UploadFile | None = form.get("photo") if hasattr(form.get("photo"), "read") else None
        text = (form.get("text") or "").strip()[:5000]
        figure = form.get("figure") if form.get("figure") in FIGURE_ORDER else jobs.current_figure(child)[0]
        reply = Reply(child_id=child.id, figure=figure, typed_text=text)
        if photo and photo.filename:
            data = await photo.read(MAX_UPLOAD + 1)
            if photo.content_type not in PHOTO_TYPES or len(data) > MAX_UPLOAD:
                flash(request, "Please upload a JPG, PNG or WebP photo under 10 MB.")
                return RedirectResponse("/account", status_code=303)
            path = settings.uploads_dir / f"{secrets.token_hex(16)}{PHOTO_TYPES[photo.content_type]}"
            path.write_bytes(data)
            reply.photo_path = str(path)
        elif not text:
            flash(request, "Add a photo of the letter, or type it in.")
            return RedirectResponse("/account", status_code=303)
        db.add(reply)
        db.commit()
        flash(request, f"Thank you! {FIGURE_NAMES.get(figure, 'Your pen pal')} will read {child.first_name}'s "
                       "letter and answer it in the next letter.")
        return RedirectResponse("/account", status_code=303)

    @app.get("/letters/{letter_id}.pdf")
    def letter_pdf(request: Request, letter_id: int, user: User = Depends(require_user),
                   db: Session = Depends(db_session)):
        letter = db.get(Letter, letter_id)
        if not letter or (letter.child.user_id != user.id and not user.is_admin):
            raise HTTPException(404)
        if not user.is_admin and letter.status not in ("sent", "awaiting_parent"):
            raise HTTPException(404)
        if not letter.pdf_path or not Path(letter.pdf_path).exists():
            raise HTTPException(404)
        return FileResponse(letter.pdf_path, media_type="application/pdf")

    # --- parent preview -----------------------------------------------------

    def _parent_letter(db: Session, user: User, letter_id: int, statuses=("awaiting_parent",)) -> Letter:
        letter = db.get(Letter, letter_id)
        if not letter or letter.child.user_id != user.id or letter.status not in statuses:
            raise HTTPException(404)
        return letter

    @app.get("/letters/{letter_id}.html", response_class=HTMLResponse)
    def letter_html(letter_id: int, user: User = Depends(require_user), db: Session = Depends(db_session)):
        letter = db.get(Letter, letter_id)
        allowed = user.is_admin or (letter and letter.child.user_id == user.id and letter.status in ("sent", "awaiting_parent"))
        if not letter or not allowed or not letter.html_path or not Path(letter.html_path).exists():
            raise HTTPException(404)
        return HTMLResponse(Path(letter.html_path).read_text(encoding="utf-8"),
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                     "font-src data:; img-src data:; script-src 'unsafe-inline'"})

    @app.get("/letters/{letter_id}/preview", response_class=HTMLResponse)
    def preview(request: Request, letter_id: int, user: User = Depends(require_user), db: Session = Depends(db_session)):
        letter = db.get(Letter, letter_id)
        if not letter or letter.child.user_id != user.id:
            raise HTTPException(404)
        if letter.status != "awaiting_parent":
            flash(request, "That letter has already been posted." if letter.status == "sent"
                  else "That letter isn't waiting for you any more.")
            return RedirectResponse("/account", status_code=303)
        return page(request, "preview.html", letter=letter, rewrites_left=jobs.parent_rewrites_left(db, letter.child),
                    hours_left=max(int((letter.preview_deadline - models_now()).total_seconds() // 3600), 0))

    @app.post("/letters/{letter_id}/approve", dependencies=[Depends(check_csrf)])
    def parent_approve(request: Request, letter_id: int, user: User = Depends(require_user),
                       db: Session = Depends(db_session)):
        letter = _parent_letter(db, user, letter_id)
        jobs.send_letter(db, settings, letter, app.state.services)
        flash(request, f"Thank you! {letter.child.first_name}'s letter is on its way." if letter.status == "sent"
              else "We couldn't post that letter just now. We'll sort it out and post it shortly.")
        return RedirectResponse("/account", status_code=303)

    @app.post("/letters/{letter_id}/changes", dependencies=[Depends(check_csrf)])
    def parent_changes(request: Request, letter_id: int, user: User = Depends(require_user),
                       db: Session = Depends(db_session), note: str = Form("")):
        letter = _parent_letter(db, user, letter_id)
        note = note.strip()[:500]
        if not note:
            flash(request, "Tell us what you'd like changed, so the new letter gets it right.")
            return RedirectResponse(f"/letters/{letter_id}/preview", status_code=303)
        if jobs.parent_rewrites_left(db, letter.child) <= 0:
            flash(request, "You've used this month's rewrites. Approve this letter, or reply to our email and we'll help.")
            return RedirectResponse(f"/letters/{letter_id}/preview", status_code=303)
        jobs.parent_rewrite(db, letter, note)
        flash(request, "Thanks, we'll write a new letter with your changes. It will be checked again, and we'll email "
                       "you to preview it within a couple of days.")
        return RedirectResponse("/account", status_code=303)

    @app.post("/children/{child_id}/settings", dependencies=[Depends(check_csrf)])
    def child_settings(request: Request, child_id: int, user: User = Depends(require_user),
                       db: Session = Depends(db_session), parent_preview: bool = Form(False)):
        child = db.get(Child, child_id)
        if not child or child.user_id != user.id:
            raise HTTPException(404)
        child.parent_preview = parent_preview
        db.commit()
        flash(request, f"Saved. You'll {'preview' if parent_preview else 'no longer preview'} "
                       f"{child.first_name}'s letters before they're posted.")
        return RedirectResponse("/account", status_code=303)

    @app.get("/account/export")
    def export(user: User = Depends(require_user)):
        data = {
            "account": {"name": user.name, "email": user.email, "created_at": str(user.created_at),
                        "marketing_opt_in": user.marketing_opt_in},
            "children": [{
                "first_name": c.first_name, "age": c.age, "interests": c.interests, "avoid": c.avoid,
                "address": c.address(),
                "subscription": {"plan": c.subscription.plan, "status": c.subscription.status} if c.subscription else None,
                "letters": [{"figure": l.figure, "kind": l.kind, "status": l.status, "created_at": str(l.created_at),
                             "letter": json.loads(l.letter_json) if l.letter_json else None} for l in c.letters],
                "replies": [{"figure": r.figure, "created_at": str(r.created_at), "typed_text": r.typed_text,
                             "transcription": json.loads(r.transcription_json) if r.transcription_json else None}
                            for r in c.replies],
            } for c in user.children],
        }
        return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=letters-from-history-data.json"})

    @app.post("/account/delete", dependencies=[Depends(check_csrf)])
    def delete_account(request: Request, user: User = Depends(require_user), db: Session = Depends(db_session),
                       confirm: str = Form("")):
        if confirm.strip().upper() != "DELETE":
            flash(request, 'Type DELETE to confirm.')
            return RedirectResponse("/account", status_code=303)
        user = db.merge(user)
        if any(s.stripe_subscription_id for s in user.subscriptions):
            billing.cancel_all(settings, user)
        for child in user.children:
            for reply in child.replies:
                if reply.photo_path:
                    Path(reply.photo_path).unlink(missing_ok=True)
            for letter in child.letters:
                for p in (letter.pdf_path, letter.html_path):
                    if p:
                        Path(p).unlink(missing_ok=True)
        db.delete(user)
        db.commit()
        request.session.clear()
        return RedirectResponse("/?deleted=1", status_code=303)

    # --- Stripe webhook -----------------------------------------------------

    @app.post("/stripe/webhook")
    async def stripe_webhook(request: Request, db: Session = Depends(db_session)):
        payload = await request.body()
        try:
            event = billing.parse_event(settings, payload, request.headers.get("stripe-signature"))
        except Exception:  # noqa: BLE001 - bad signature or payload
            return Response(status_code=400)
        outcome = billing.handle_event(db, event)
        log.info("stripe %s %s: %s", event["type"], event["id"], outcome)
        return {"received": True}

    # --- daily job (called by a scheduler) -----------------------------------

    @app.post("/internal/run-due")
    def run_due_endpoint(request: Request, db: Session = Depends(db_session)):
        expected = f"Bearer {settings.cron_secret}"
        if not settings.cron_secret or not secrets.compare_digest(request.headers.get("authorization", ""), expected):
            raise HTTPException(404)
        summary = jobs.run_due(db, settings, app.state.services)
        return {"letters_written": summary.letters_written, "letters_sent": summary.letters_sent,
                "to_review": summary.to_review, "replies_read": summary.replies_read, "errors": summary.errors}

    # --- admin --------------------------------------------------------------

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request, admin: User = Depends(require_admin), db: Session = Depends(db_session)):
        review = db.scalars(select(Letter).where(Letter.status.in_(["needs_review", "held", "failed"]))
                            .order_by(Letter.created_at)).all()
        held_replies = db.scalars(select(Reply).where(Reply.status == "held").order_by(Reply.created_at)).all()
        recent = db.scalars(select(Letter).where(Letter.status == "sent").order_by(Letter.sent_at.desc()).limit(20)).all()
        with_parents = db.scalars(select(Letter).where(Letter.status == "awaiting_parent")
                                  .order_by(Letter.preview_deadline)).all()
        subs = db.scalars(select(Subscription)).all()
        counts = {s: sum(1 for x in subs if x.status == s) for s in ("active", "pending", "past_due", "canceled")}
        return page(request, "admin.html", review=review, held_replies=held_replies, recent=recent, counts=counts,
                    with_parents=with_parents,
                    live_mail=settings.live_mail, review_all=settings.review_all,
                    checks={l.id: json.loads(l.checks_json or "[]") for l in review},
                    letters={l.id: json.loads(l.letter_json) if l.letter_json else None for l in review},
                    replies={r.id: json.loads(r.transcription_json) if r.transcription_json else None
                             for r in held_replies})

    def _admin_letter(db: Session, letter_id: int) -> Letter:
        letter = db.get(Letter, letter_id)
        if not letter:
            raise HTTPException(404)
        return letter

    @app.post("/admin/letters/{letter_id}/approve", dependencies=[Depends(check_csrf)])
    def approve(request: Request, letter_id: int, admin: User = Depends(require_admin),
                db: Session = Depends(db_session)):
        letter = _admin_letter(db, letter_id)
        if letter.status not in ("needs_review", "failed") or not letter.letter_json:
            flash(request, "That letter can't be sent from here.")
            return RedirectResponse("/admin", status_code=303)
        letter.status = "approved"
        db.commit()
        jobs.release_letter(db, settings, letter, app.state.services)
        outcome = "sent to the parent to preview" if letter.status == "awaiting_parent" else letter.status
        flash(request, f"Letter {letter.id}: {outcome}" + (f" (proof: {letter.proof_url})" if letter.proof_url else ""))
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/letters/{letter_id}/reject", dependencies=[Depends(check_csrf)])
    def reject(request: Request, letter_id: int, admin: User = Depends(require_admin),
               db: Session = Depends(db_session)):
        letter = _admin_letter(db, letter_id)
        jobs.reject_letter(db, letter)
        flash(request, f"Letter {letter.id} rejected. A fresh one will be written on the next run.")
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/replies/{reply_id}/{action}", dependencies=[Depends(check_csrf)])
    def reply_action(request: Request, reply_id: int, action: str, admin: User = Depends(require_admin),
                     db: Session = Depends(db_session)):
        reply = db.get(Reply, reply_id)
        if not reply or reply.status != "held" or action not in ("release", "dismiss"):
            raise HTTPException(404)
        reply.status = "ready" if action == "release" else "dismissed"
        if reply.photo_path:  # reviewed: the photo is no longer needed
            Path(reply.photo_path).unlink(missing_ok=True)
            reply.photo_path = None
        db.commit()
        flash(request, f"Reply {reply.id} {'released for an answer' if action == 'release' else 'dismissed'}.")
        return RedirectResponse("/admin", status_code=303)

    @app.get("/admin/replies/{reply_id}/photo")
    def held_photo(reply_id: int, admin: User = Depends(require_admin), db: Session = Depends(db_session)):
        reply = db.get(Reply, reply_id)
        if not reply or reply.status != "held" or not reply.photo_path or not Path(reply.photo_path).exists():
            raise HTTPException(404)
        return FileResponse(reply.photo_path, headers={"Cache-Control": "no-store"})

    @app.post("/admin/run", dependencies=[Depends(check_csrf)])
    def run_now(request: Request, admin: User = Depends(require_admin), db: Session = Depends(db_session)):
        summary = jobs.run_due(db, settings, app.state.services)
        flash(request, f"Run finished: {summary.letters_written} written, {summary.letters_sent} sent, "
                       f"{summary.to_review} to review, {summary.replies_read} replies read"
                       + (f", {len(summary.errors)} errors: {'; '.join(summary.errors)[:300]}" if summary.errors else ""))
        return RedirectResponse("/admin", status_code=303)

    return app


def __getattr__(name: str):
    # `uvicorn app.web:app` builds the app on first use, from environment settings.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)

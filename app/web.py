"""The website: landing page, parent accounts, Stripe checkout, reply uploads,
the admin review queue, and the privacy pages.

Run locally:  uvicorn app.web:app --reload
"""

import json
import logging
import re
import secrets
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from letters import render

from . import billing, gifts, google_auth, jobs, ratelimit
from .seasonal import christmas_notice
from .config import COMPANY, FIGURE_ORDER, GIFT_OPTIONS, GIFT_SEND_AHEAD_DAYS, PLANS, PREVIEW_HOURS, PRODUCT, REPLY_UPLOADS_PER_MONTH, Settings
from .emails import send_email
from .models import Child, Gift, Letter, Reply, SafeguardingRecord, Subscription, User, make_sessionmaker
from .models import now as models_now
from .security import (csrf_ok, csrf_token, hash_password, make_reset_token, make_verify_token, read_reset_token,
                       read_unsubscribe_token, read_verify_token, verify_password)
from .storage import content_type, get_storage

log = logging.getLogger("letters.web")
SOURCE_CHARS = re.compile(r"[^a-z0-9_.\-]+")
HERE = Path(__file__).parent
MAX_UPLOAD = 10 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
FIGURE_NAMES = {k: v[0] for k, v in render.FIGURE_NAMES.items()}
STATUS_LABELS = {
    "needs_review": "Being checked", "held": "Being checked", "approved": "Ready to post",
    "awaiting_parent": "Waiting for your OK", "parent_rewrite": "Rewritten at your request",
    "sent": "Posted", "rejected": "Rewritten", "failed": "Delayed",
}


def init_error_alerts(settings: Settings) -> None:
    """Optional Sentry error alerts, set up so that no children's data can be sent:
    no request bodies, no cookies or IP addresses, and no local variables from stack frames."""
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        log.warning("SENTRY_DSN is set but sentry-sdk isn't installed: pip install '.[monitoring]'")
        return
    sentry_sdk.init(dsn=settings.sentry_dsn, send_default_pii=False, max_request_body_size="never",
                    include_local_variables=False, traces_sample_rate=0)


# Forms that redirect to Stripe need those hosts in form-action. Letter previews set a stricter policy of their own.
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
       "script-src 'self' 'unsafe-inline'; frame-src 'self'; frame-ancestors 'self'; base-uri 'self'; "
       "object-src 'none'; form-action 'self' https://checkout.stripe.com https://billing.stripe.com "
       "https://accounts.google.com")


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def create_app(settings: Settings | None = None, services: jobs.Services | None = None) -> FastAPI:
    settings = settings or Settings()
    init_error_alerts(settings)
    SessionLocal = make_sessionmaker(settings.database_url, create_tables=settings.auto_create_tables)
    storage = get_storage(settings)
    app = FastAPI(title=PRODUCT, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.SessionLocal = SessionLocal
    app.state.services = services or jobs.Services()
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                       https_only=settings.secure_cookies, max_age=60 * 60 * 24 * 30)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CSP)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if settings.secure_cookies:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(PREVIEW_HOURS=PREVIEW_HOURS, COMPANY=COMPANY, PRODUCT=PRODUCT, PLANS=PLANS, FIGURE_NAMES=FIGURE_NAMES,
                                 STATUS_LABELS=STATUS_LABELS, contact_email=settings.contact_email,
                                 legal=settings, POLICY_DATE="11 October 2026",
                                 google_enabled=google_auth.enabled(settings))

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

    def remember_source(request: Request) -> None:
        """First visit only: note which link brought this person (?ref= or utm_source), so the admin can
        see which channels lead to paying families. Kept in the session cookie until they sign up."""
        if "src" in request.session:
            return
        q = request.query_params
        source = SOURCE_CHARS.sub("-", (q.get("ref") or q.get("utm_source") or "").lower()).strip("-")[:100]
        if not source:
            return
        campaign = "/".join(SOURCE_CHARS.sub("-", (q.get(k) or "").lower()).strip("-")
                            for k in ("utm_medium", "utm_campaign") if q.get(k))[:200]
        request.session["src"] = {"source": source, "campaign": campaign, "landing": request.url.path[:200]}

    def source_fields(request: Request) -> dict:
        src = request.session.get("src") or {}
        return {"source": src.get("source", ""), "campaign": src.get("campaign", "")}

    def page(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
        remember_source(request)
        db = SessionLocal()
        try:
            user = current_user(request, db)
            flashes = request.session.pop("flash", [])
            return templates.TemplateResponse(request, template, {
                "user": user, "csrf": csrf_token(request.session), "flashes": flashes,
                "redeeming": request.session.get("gift_code"),
                "christmas": christmas_notice(models_now().date()), **context},
                status_code=status_code)
        finally:
            db.close()

    def flash(request: Request, message: str) -> None:
        request.session.setdefault("flash", []).append(message)

    def login(request: Request, user: User) -> None:
        gift_code = request.session.get("gift_code")  # a parent part-way through redeeming a gift
        request.session.clear()
        request.session["uid"] = user.id
        if gift_code:
            request.session["gift_code"] = gift_code

    def after_login(request: Request, user: User) -> str:
        if request.session.get("gift_code"):
            return "/children/new"
        return "/admin" if user.is_admin else "/account"

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def limited(request: Request, db: Session, action: str, who: str) -> bool:
        """True if this attempt is over the limit (and should be refused)."""
        return settings.rate_limits and not ratelimit.allow(db, action, who)

    def too_many(request: Request) -> HTMLResponse:
        return page(request, "message.html", 429, title="Please wait a little",
                    message="There have been too many attempts. Please wait a few minutes and try again.")

    def send_verification(user: User) -> None:
        token = make_verify_token(settings.secret_key, user.id, user.email)
        send_email(settings, user.email, "Please confirm your email for Letters from History",
                   f"Hello {user.name},\n\nPlease confirm your email address by opening this link:\n"
                   f"{settings.base_url}/verify/{token}\n\nWe need this before you can subscribe, so we know "
                   "letters and preview emails reach the right person.\n\nLetters from History")

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
        if limited(request, db, "signup_ip", client_ip(request)):
            return too_many(request)
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
                    marketing_opt_in=marketing, landing=(request.session.get("src") or {}).get("landing", ""),
                    **source_fields(request))
        db.add(user)
        db.commit()
        send_verification(user)
        login(request, user)
        return RedirectResponse(f"/children/new?plan={plan}", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return page(request, "login.html")

    @app.post("/login", dependencies=[Depends(check_csrf)])
    def login_post(request: Request, db: Session = Depends(db_session), email: str = Form(...),
                   password: str = Form(...)):
        if limited(request, db, "login_ip", client_ip(request)) or limited(request, db, "login_email", email.strip()):
            return too_many(request)
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if not user or not verify_password(password, user.password_hash):
            return page(request, "login.html", 400, errors=["That email and password don't match."], email=email)
        login(request, user)
        return RedirectResponse(after_login(request, user), status_code=303)

    # --- Sign in with Google ---------------------------------------------------

    @app.get("/auth/google")
    def google_start(request: Request, plan: str = "penpal"):
        if not google_auth.enabled(settings):
            raise HTTPException(404)
        request.session["google_plan"] = plan if plan in PLANS else "penpal"
        return RedirectResponse(google_auth.authorization_url(settings, request.session), status_code=303)

    @app.get("/auth/google/callback")
    def google_callback(request: Request, db: Session = Depends(db_session), code: str = "", state: str = "",
                        error: str = ""):
        if not google_auth.enabled(settings):
            raise HTTPException(404)
        expected = request.session.pop("google_state", None)
        if error or not code or not expected or not secrets.compare_digest(state, expected):
            flash(request, "Google sign-in didn't complete. Please try again.")
            return RedirectResponse("/login", status_code=303)
        if limited(request, db, "login_ip", client_ip(request)):
            return too_many(request)
        try:
            profile = google_auth.fetch_profile(settings, code)
        except RuntimeError:
            log.exception("Google sign-in failed")
            flash(request, "We couldn't sign you in with Google just now. Please try again.")
            return RedirectResponse("/login", status_code=303)
        if not profile["email"] or not profile["email_verified"]:
            flash(request, "Your Google account's email isn't verified, so please sign up with email and a password.")
            return RedirectResponse("/signup", status_code=303)
        user = db.scalar(select(User).where(User.google_sub == profile["sub"]))
        if not user:
            # Link to an existing account with the same (Google-verified) email.
            user = db.scalar(select(User).where(User.email == profile["email"]))
            if user and user.google_sub and user.google_sub != profile["sub"]:
                flash(request, "That email is linked to a different Google account. Please log in with your password.")
                return RedirectResponse("/login", status_code=303)
            if user:
                user.google_sub = profile["sub"]
        if user:
            if not user.email_verified_at:
                user.email_verified_at = models_now()
            db.commit()
            login(request, user)
            return RedirectResponse(after_login(request, user), status_code=303)
        # New parent: they still confirm the privacy policy and terms before an account exists.
        plan = request.session.get("google_plan", "penpal")
        request.session["google_profile"] = profile
        request.session["google_plan"] = plan
        return RedirectResponse("/signup/google", status_code=303)

    @app.get("/signup/google", response_class=HTMLResponse)
    def google_signup_form(request: Request):
        profile = request.session.get("google_profile")
        if not profile:
            return RedirectResponse("/signup", status_code=303)
        return page(request, "signup_google.html", profile=profile)

    @app.post("/signup/google", dependencies=[Depends(check_csrf)])
    def google_signup(request: Request, db: Session = Depends(db_session), name: str = Form(""),
                      agree: bool = Form(False), marketing: bool = Form(False)):
        profile = request.session.get("google_profile")
        if not profile:
            return RedirectResponse("/signup", status_code=303)
        if not agree:
            return page(request, "signup_google.html", 400, profile=profile,
                        errors=["Please confirm you've read the privacy policy and terms."])
        if limited(request, db, "signup_ip", client_ip(request)):
            return too_many(request)
        plan = request.session.get("google_plan", "penpal")
        if db.scalar(select(User).where((User.google_sub == profile["sub"]) | (User.email == profile["email"]))):
            flash(request, "You already have an account. Please sign in.")
            return RedirectResponse("/login", status_code=303)
        user = User(email=profile["email"], name=(name.strip() or profile["name"] or "there")[:120], password_hash="",
                    google_sub=profile["sub"], email_verified_at=models_now(), marketing_opt_in=marketing,
                    landing=(request.session.get("src") or {}).get("landing", ""), **source_fields(request))
        db.add(user)
        db.commit()
        login(request, user)
        return RedirectResponse(f"/children/new?plan={plan}", status_code=303)

    @app.post("/logout", dependencies=[Depends(check_csrf)])
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/forgot", response_class=HTMLResponse)
    def forgot_form(request: Request):
        return page(request, "forgot.html")

    @app.post("/forgot", dependencies=[Depends(check_csrf)])
    def forgot(request: Request, db: Session = Depends(db_session), email: str = Form(...)):
        if limited(request, db, "forgot_ip", client_ip(request)) or limited(request, db, "forgot_email", email.strip()):
            return too_many(request)
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

    @app.get("/verify/{token}")
    def verify_email(request: Request, token: str, db: Session = Depends(db_session)):
        data = read_verify_token(settings.secret_key, token)
        user = db.get(User, data["u"]) if data else None
        if not user or user.email != data["e"]:
            return page(request, "message.html", 400, title="That link has expired",
                        message="Log in and we'll send you a new confirmation link.")
        if not user.email_verified_at:
            user.email_verified_at = models_now()
            db.commit()
        flash(request, "Thank you, your email is confirmed.")
        return RedirectResponse("/account" if request.session.get("uid") == user.id else "/login", status_code=303)

    @app.post("/verify/resend", dependencies=[Depends(check_csrf)])
    def resend_verification(request: Request, user: User = Depends(require_user), db: Session = Depends(db_session)):
        if user.email_verified_at:
            return RedirectResponse("/account", status_code=303)
        if limited(request, db, "verify_resend", str(user.id)):
            return too_many(request)
        send_verification(user)
        flash(request, f"We've sent a new confirmation link to {user.email}.")
        return RedirectResponse(request.headers.get("referer") or "/account", status_code=303)

    # --- gifts ---------------------------------------------------------------

    @app.get("/gift", response_class=HTMLResponse)
    def gift_form(request: Request, cancelled: str = ""):
        if cancelled:
            flash(request, "Payment was cancelled, so nothing was charged. You can try again below.")
        return page(request, "gift.html", options=GIFT_OPTIONS, form={"months": 6, "delivery": "me"})

    @app.post("/gift", dependencies=[Depends(check_csrf)])
    def gift_buy(request: Request, db: Session = Depends(db_session), months: int = Form(0),
                 buyer_name: str = Form(""), buyer_email: str = Form(""), child_first_name: str = Form(""),
                 message: str = Form(""), agree: bool = Form(False), delivery: str = Form("me"),
                 recipient_email: str = Form(""), send_on: str = Form("")):
        form = dict(months=months, buyer_name=buyer_name, buyer_email=buyer_email, child_first_name=child_first_name,
                    message=message, delivery=delivery, recipient_email=recipient_email, send_on=send_on)
        if limited(request, db, "gift_ip", client_ip(request)):
            return too_many(request)
        email = buyer_email.strip().lower()
        errors = []
        if months not in GIFT_OPTIONS:
            errors.append("Choose how many months to give.")
        if not buyer_name.strip():
            errors.append("Enter your name, so the family knows who it's from.")
        if "@" not in email or "." not in email.split("@")[-1]:
            errors.append("Enter a valid email address: we send the gift certificate there.")
        if len(message) > 300:
            errors.append("Keep the message to 300 characters so it fits on the certificate.")
        send_date = None
        if delivery == "email":
            recipient = recipient_email.strip().lower()
            if "@" not in recipient or "." not in recipient.split("@")[-1]:
                errors.append("Enter the family's email address, or choose to send the gift yourself.")
            try:
                send_date = date.fromisoformat(send_on)
            except ValueError:
                errors.append("Choose the day we should email the gift.")
            else:
                if not models_now().date() <= send_date <= models_now().date() + timedelta(days=GIFT_SEND_AHEAD_DAYS):
                    errors.append(f"Choose a day between today and {GIFT_SEND_AHEAD_DAYS} days from now.")
        if not agree:
            errors.append("Please confirm you've read the privacy policy and agree to the terms.")
        if errors:
            return page(request, "gift.html", 400, errors=errors, options=GIFT_OPTIONS, form=form)
        gift = gifts.create_gift(db, months, buyer_name, email, child_first_name, message,
                                 recipient_email=recipient_email if send_date else "", send_on=send_date,
                                 **source_fields(request))
        try:
            url = gifts.checkout_url(settings, db, gift)
        except Exception:  # noqa: BLE001
            log.exception("Gift checkout failed")
            return page(request, "gift.html", 503, options=GIFT_OPTIONS, form=form,
                        errors=["We couldn't start payment just now. Please try again in a moment."])
        return RedirectResponse(url, status_code=303)

    @app.get("/gift/thanks", response_class=HTMLResponse)
    def gift_thanks(request: Request, session_id: str = "", db: Session = Depends(db_session)):
        gift = gifts.confirm_from_stripe(settings, db, session_id, app.state.services.to_pdf) if session_id else None
        if not gift:
            raise HTTPException(404)
        link = f"/gift/certificate/{gifts.certificate_token(settings, gift)}" if gift.certificate_path else None
        return page(request, "gift_thanks.html", gift=gift, certificate=link)

    @app.get("/gift/certificate/{token}")
    def gift_certificate(token: str, db: Session = Depends(db_session)):
        gift = gifts.gift_from_token(settings, db, token)
        if not gift or not gift.certificate_path or gift.status == "refunded":
            raise HTTPException(404)
        store = get_storage(settings)
        if not store.exists(gift.certificate_path):
            raise HTTPException(404)
        return Response(store.get(gift.certificate_path), media_type=content_type(gift.certificate_path),
                        headers={"Content-Disposition": 'inline; filename="letters-from-history-gift.'
                                 + gift.certificate_path.rsplit(".", 1)[-1] + '"'})

    @app.get("/redeem", response_class=HTMLResponse)
    def redeem_form(request: Request, code: str = ""):
        return page(request, "redeem.html", code=code)

    @app.post("/redeem", dependencies=[Depends(check_csrf)])
    def redeem_post(request: Request, db: Session = Depends(db_session), code: str = Form(...)):
        if limited(request, db, "redeem_ip", client_ip(request)):
            return too_many(request)
        gift, reason = gifts.find_redeemable(db, code)
        if not gift:
            return page(request, "redeem.html", 400, code=code, errors=[reason])
        request.session["gift_code"] = gift.code
        if current_user(request, db):
            return RedirectResponse("/children/new", status_code=303)
        return RedirectResponse("/signup?plan=penpal", status_code=303)

    @app.post("/redeem/cancel", dependencies=[Depends(check_csrf)])
    def redeem_cancel(request: Request):
        request.session.pop("gift_code", None)
        return RedirectResponse("/", status_code=303)

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
    def child_form(request: Request, user: User = Depends(require_user), db: Session = Depends(db_session),
                   plan: str = "penpal"):
        return page(request, "child_form.html", plan=plan if plan in PLANS else "penpal", form={},
                    gift=redeeming_gift(request, db))

    def redeeming_gift(request: Request, db: Session) -> Gift | None:
        """The gift this parent is redeeming, if the code is still good (it is checked again on use)."""
        code = request.session.get("gift_code")
        if not code:
            return None
        gift, _ = gifts.find_redeemable(db, code)
        if not gift:
            request.session.pop("gift_code", None)
        return gift

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
        gift = redeeming_gift(request, db)
        if request.session.get("gift_code") and not gift:
            errors.append("That gift code can no longer be used. Please check it on the redeem page.")
        if errors:
            return page(request, "child_form.html", 400, errors=errors, plan=plan, form=form, gift=gift)
        user = db.merge(user)
        child = Child(user_id=user.id, first_name=first_name.strip()[:60], age=age, interests=interests[:500],
                      avoid=avoid[:500], recipient_name=recipient_name.strip()[:120], line1=line1.strip()[:200],
                      line2=line2.strip()[:200], town=town.strip()[:100], postcode=postcode.strip().upper()[:12],
                      parent_preview=parent_preview)
        db.add(child)
        db.flush()
        sub = Subscription(user_id=user.id, child_id=child.id, plan="penpal" if gift else plan, status="pending")
        db.add(sub)
        db.commit()
        if gift:
            gifts.redeem(db, gift, sub, user.id)
            request.session.pop("gift_code", None)
            flash(request, f"The gift is set up! {child.first_name}'s first letter, an introduction, will be written "
                           "in the next day or two, checked, and posted.")
            return RedirectResponse("/account", status_code=303)
        return RedirectResponse(f"/checkout/{sub.id}", status_code=303)

    CHILD_FIELDS = ("first_name", "interests", "avoid", "recipient_name", "line1", "line2", "town", "postcode")

    def _own_child(db: Session, user: User, child_id: int) -> Child:
        child = db.get(Child, child_id)
        if not child or child.user_id != user.id:
            raise HTTPException(404)
        return child

    @app.get("/children/{child_id}/edit", response_class=HTMLResponse)
    def child_edit_form(request: Request, child_id: int, user: User = Depends(require_user),
                        db: Session = Depends(db_session)):
        child = _own_child(db, user, child_id)
        form = {f: getattr(child, f) for f in CHILD_FIELDS} | {"age": child.age, "parent_preview": child.parent_preview}
        return page(request, "child_form.html", editing=child, form=form, plan=None)

    @app.post("/children/{child_id}/edit", dependencies=[Depends(check_csrf)])
    def child_edit(request: Request, child_id: int, user: User = Depends(require_user),
                   db: Session = Depends(db_session), first_name: str = Form(...), age: int = Form(...),
                   interests: str = Form(""), avoid: str = Form(""), recipient_name: str = Form(...),
                   line1: str = Form(...), line2: str = Form(""), town: str = Form(...), postcode: str = Form(...),
                   parent_preview: bool = Form(False)):
        child = _own_child(db, user, child_id)
        form = dict(first_name=first_name, age=age, interests=interests, avoid=avoid, recipient_name=recipient_name,
                    line1=line1, line2=line2, town=town, postcode=postcode, parent_preview=parent_preview)
        if not 4 <= age <= 13:
            return page(request, "child_form.html", 400, editing=child, form=form, plan=None,
                        errors=["Letters are written for children aged 5 to 12."])
        child.first_name = first_name.strip()[:60]
        child.age = age
        child.interests = interests[:500]
        child.avoid = avoid[:500]
        child.recipient_name = recipient_name.strip()[:120]
        child.line1 = line1.strip()[:200]
        child.line2 = line2.strip()[:200]
        child.town = town.strip()[:100]
        child.postcode = postcode.strip().upper()[:12]
        child.parent_preview = parent_preview
        db.commit()
        flash(request, f"Saved. Letters that haven't been posted yet will use {child.first_name}'s new details.")
        return RedirectResponse("/account", status_code=303)

    @app.get("/checkout/{sub_id}", response_class=HTMLResponse)
    def checkout_page(request: Request, sub_id: int, user: User = Depends(require_user),
                      db: Session = Depends(db_session)):
        sub = db.get(Subscription, sub_id)
        if not sub or sub.user_id != user.id:
            raise HTTPException(404)
        return page(request, "checkout.html", sub=sub, trial_end=billing.gift_trial_end(sub))

    @app.post("/checkout/{sub_id}", dependencies=[Depends(check_csrf)])
    def checkout(request: Request, sub_id: int, user: User = Depends(require_user),
                 db: Session = Depends(db_session)):
        sub = db.get(Subscription, sub_id)
        if not sub or sub.user_id != user.id:
            raise HTTPException(404)
        if not billing.can_checkout(sub):
            flash(request, "That subscription is already active.")
            return RedirectResponse("/account", status_code=303)
        if not user.email_verified_at:
            flash(request, f"Please confirm your email first: open the link we sent to {user.email}.")
            return RedirectResponse(f"/checkout/{sub_id}", status_code=303)
        try:
            url = billing.checkout_url(settings, db, db.merge(user), sub)
        except Exception:  # noqa: BLE001
            log.exception("Checkout failed")
            flash(request, "We couldn't start checkout just now. Please try again in a moment.")
            return RedirectResponse(f"/checkout/{sub_id}", status_code=303)
        return RedirectResponse(url, status_code=303)

    def _own_paid_sub(db: Session, user: User, sub_id: int) -> Subscription:
        sub = db.get(Subscription, sub_id)
        if not sub or sub.user_id != user.id or not sub.stripe_subscription_id:
            raise HTTPException(404)
        return sub

    @app.get("/subscriptions/{sub_id}/cancel", response_class=HTMLResponse)
    def cancel_page(request: Request, sub_id: int, user: User = Depends(require_user),
                    db: Session = Depends(db_session)):
        return page(request, "cancel.html", sub=_own_paid_sub(db, user, sub_id))

    @app.post("/subscriptions/{sub_id}/cancel", dependencies=[Depends(check_csrf)])
    def cancel_subscription(request: Request, sub_id: int, user: User = Depends(require_user),
                            db: Session = Depends(db_session)):
        sub = _own_paid_sub(db, user, sub_id)
        if sub.status not in ("canceled", "incomplete_expired") and not sub.cancel_at:
            try:
                ends = billing.set_cancel_at_period_end(settings, sub, True)
            except Exception:  # noqa: BLE001
                log.exception("Cancel failed")
                flash(request, "We couldn't cancel just now. Please try again, or email us and we'll do it for you.")
                return RedirectResponse("/account", status_code=303)
            db.commit()
            when = f"{ends:%-d %B %Y}" if ends else "the end of the month you've paid for"
            send_email(settings, user.email, f"{sub.child.first_name}'s subscription is cancelled",
                       f"Hello {user.name},\n\nYou've cancelled {sub.child.first_name}'s {PLANS[sub.plan]['name']} "
                       f"subscription. You won't be charged again. Letters already due are still posted, and the "
                       f"subscription ends on {when}.\n\nChanged your mind? You can keep it from your account "
                       f"before then: {settings.base_url}/account\n\nLetters from History")
            flash(request, f"Cancelled. You won't be charged again. {sub.child.first_name}'s subscription ends on {when}. "
                           "We've emailed you a confirmation.")
        return RedirectResponse("/account", status_code=303)

    @app.post("/subscriptions/{sub_id}/keep", dependencies=[Depends(check_csrf)])
    def keep_subscription(request: Request, sub_id: int, user: User = Depends(require_user),
                          db: Session = Depends(db_session)):
        sub = _own_paid_sub(db, user, sub_id)
        if sub.cancel_at and sub.cancel_at > models_now():
            try:
                billing.set_cancel_at_period_end(settings, sub, False)
            except Exception:  # noqa: BLE001
                log.exception("Undo cancel failed")
                flash(request, "We couldn't do that just now. Please try again in a moment.")
                return RedirectResponse("/account", status_code=303)
            db.commit()
            flash(request, f"Great, {sub.child.first_name}'s letters will carry on.")
        return RedirectResponse("/account", status_code=303)

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
        if limited(request, db, "upload_user", str(user.id)):
            return too_many(request)
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
            key = f"uploads/{secrets.token_hex(16)}{PHOTO_TYPES[photo.content_type]}"
            storage.put(key, data, photo.content_type)
            reply.photo_path = key
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
        if not letter.pdf_path or not storage.exists(letter.pdf_path):
            raise HTTPException(404)
        return Response(storage.get(letter.pdf_path), media_type="application/pdf",
                        headers={"Cache-Control": "private, no-store"})

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
        if not letter or not allowed or not letter.html_path or not storage.exists(letter.html_path):
            raise HTTPException(404)
        return HTMLResponse(storage.get(letter.html_path).decode("utf-8"),
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
        if limited(request, db, "changes_user", str(user.id)):
            return too_many(request)
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

    @app.post("/account/preferences", dependencies=[Depends(check_csrf)])
    def preferences(request: Request, user: User = Depends(require_user), db: Session = Depends(db_session),
                    marketing: bool = Form(False)):
        user.marketing_opt_in = marketing
        db.commit()
        flash(request, "Saved. " + ("We'll send you occasional news and offers." if marketing
                                    else "You won't get news or offers. You'll still get emails about your letters."))
        return RedirectResponse("/account", status_code=303)

    @app.get("/unsubscribe/{token}", response_class=HTMLResponse)
    def unsubscribe_page(request: Request, token: str, db: Session = Depends(db_session)):
        user_id = read_unsubscribe_token(settings.secret_key, token)
        if not user_id or not db.get(User, user_id):
            return page(request, "message.html", 400, title="That link doesn't work",
                        message="Log in and change your email preferences in your account instead.")
        return page(request, "unsubscribe.html", token=token)

    @app.post("/unsubscribe/{token}")
    def unsubscribe(request: Request, token: str, db: Session = Depends(db_session)):
        # The signed token is the proof, so this works from an email (including one-click unsubscribe).
        user = db.get(User, read_unsubscribe_token(settings.secret_key, token) or 0)
        if not user:
            raise HTTPException(404)
        user.marketing_opt_in = False
        db.commit()
        return page(request, "message.html", title="You've been unsubscribed",
                    message="You won't get news or offers from us any more. You'll still get emails about your letters.")

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
                    storage.delete(reply.photo_path)
            for letter in child.letters:
                for key in (letter.pdf_path, letter.html_path):
                    if key:
                        storage.delete(key)
        # Safeguarding records are kept as long as needed to protect the child, even after deletion.
        for child in user.children:
            for record in db.scalars(select(SafeguardingRecord).where(SafeguardingRecord.child_id == child.id)):
                record.notes += f"\n[{models_now():%d %b %Y}] Parent deleted their account."
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

        def gift_paid(gift_id: int, payment_intent: str | None) -> None:
            gift = db.get(Gift, gift_id)
            if gift:
                gifts.mark_paid(settings, db, gift, payment_intent, app.state.services.to_pdf)

        outcome = billing.handle_event(db, event, settings.stripe_prices, gift_paid=gift_paid)
        log.info("stripe %s %s: %s", event["type"], event["id"], outcome)
        return {"received": True}

    # --- daily job (called by a scheduler) -----------------------------------

    @app.post("/internal/run-due")
    def run_due_endpoint(request: Request, db: Session = Depends(db_session)):
        expected = f"Bearer {settings.cron_secret}"
        if not settings.cron_secret or not secrets.compare_digest(request.headers.get("authorization", ""), expected):
            raise HTTPException(404)
        summary = jobs.run_due(db, settings, app.state.services)
        return {"skipped": summary.skipped, "letters_written": summary.letters_written, "letters_sent": summary.letters_sent,
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
        paused = db.scalars(select(Child).where(Child.paused_at.is_not(None)).order_by(Child.paused_at)).all()
        subs = db.scalars(select(Subscription)).all()
        counts = {s: sum(1 for x in subs if x.status == s) for s in ("active", "pending", "past_due", "canceled")}
        recent_gifts = db.scalars(select(Gift).where(Gift.status != "pending")
                                  .order_by(Gift.created_at.desc()).limit(30)).all()
        return page(request, "admin.html", review=review, recent_gifts=recent_gifts, held_replies=held_replies, recent=recent, counts=counts,
                    with_parents=with_parents, paused=paused,
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
        if letter.child.paused_at:
            flash(request, f"Letters to {letter.child.first_name} are paused. Resume them first.")
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
                     db: Session = Depends(db_session), notes: str = Form(""), pause: bool = Form(False)):
        reply = db.get(Reply, reply_id)
        if not reply or reply.status != "held" or action not in ("release", "dismiss"):
            raise HTTPException(404)
        if not notes.strip():
            flash(request, "Write down what you saw and what you did before closing a held reply (safeguarding record).")
            return RedirectResponse("/admin", status_code=303)
        jobs.record_safeguarding(db, reply.child, "released" if action == "release" else "dismissed", notes,
                                 admin.email, reply=reply)
        if pause and not reply.child.paused_at:
            jobs.set_paused(db, reply.child, True, f"Held reply #{reply.id}: {notes}", admin.email)
        reply.status = "ready" if action == "release" else "dismissed"
        if reply.photo_path:  # reviewed: the photo is no longer needed
            storage.delete(reply.photo_path)
            reply.photo_path = None
        db.commit()
        flash(request, f"Reply {reply.id} {'released for an answer' if action == 'release' else 'dismissed'}.")
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/children/{child_id}/pause", dependencies=[Depends(check_csrf)])
    def pause_child(request: Request, child_id: int, admin: User = Depends(require_admin),
                    db: Session = Depends(db_session), reason: str = Form(""), resume: bool = Form(False)):
        child = db.get(Child, child_id)
        if not child:
            raise HTTPException(404)
        if not reason.strip():
            flash(request, "Give a reason: it goes into the safeguarding record.")
            return RedirectResponse("/admin", status_code=303)
        jobs.set_paused(db, child, not resume, reason, admin.email)
        flash(request, f"Letters to {child.first_name} (child #{child.id}) {'resumed' if resume else 'paused'}.")
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/letters/{letter_id}/resend", dependencies=[Depends(check_csrf)])
    def resend(request: Request, letter_id: int, admin: User = Depends(require_admin),
               db: Session = Depends(db_session)):
        letter = _admin_letter(db, letter_id)
        try:
            jobs.resend_letter(db, settings, letter, app.state.services)
            flash(request, f"Letter {letter.id} resent to {letter.child.first_name}.")
        except Exception as e:  # noqa: BLE001
            flash(request, f"Couldn't resend letter {letter.id}: {e}")
        return RedirectResponse("/admin", status_code=303)

    @app.get("/admin/sources", response_class=HTMLResponse)
    def sources_report(request: Request, days: int = 90, admin: User = Depends(require_admin),
                       db: Session = Depends(db_session)):
        """Which links bring paying families: sign-ups, paying and still-paying, and gifts, by source."""
        since = models_now() - timedelta(days=days) if days > 0 else None
        users = db.scalars(select(User).where(User.is_admin.is_(False))).all()
        gifts_sold = db.scalars(select(Gift).where(Gift.status.in_(["paid", "redeemed", "expired"]))).all()
        rows: dict[str, dict] = {}

        def row(source: str) -> dict:
            return rows.setdefault(source or "(direct or unknown)", {
                "signups": 0, "paying": 0, "active": 0, "monthly_pence": 0, "gifts": 0, "gift_pence": 0, "campaigns": set()})

        for u in users:
            if since and u.created_at < since:
                continue
            r = row(u.source)
            r["signups"] += 1
            if u.campaign:
                r["campaigns"].add(u.campaign)
            paid = [sub for sub in u.subscriptions if sub.started_at]
            if paid:
                r["paying"] += 1
            live = [sub for sub in paid if sub.status in ("active", "trialing", "past_due") and not sub.cancel_at]
            if live:
                r["active"] += 1
                r["monthly_pence"] += sum(PLANS[sub.plan]["pence"] for sub in live)
        for g in gifts_sold:
            if since and g.created_at < since:
                continue
            r = row(g.source)
            r["gifts"] += 1
            r["gift_pence"] += g.price_pence
        table = sorted(rows.items(), key=lambda kv: (-kv[1]["paying"] - kv[1]["gifts"], -kv[1]["signups"]))
        return page(request, "sources.html", table=table, days=days)

    @app.get("/admin/safeguarding", response_class=HTMLResponse)
    def safeguarding_log(request: Request, admin: User = Depends(require_admin), db: Session = Depends(db_session)):
        records = db.scalars(select(SafeguardingRecord).order_by(SafeguardingRecord.created_at.desc())).all()
        return page(request, "safeguarding_log.html", records=records)

    @app.get("/admin/replies/{reply_id}/photo")
    def held_photo(reply_id: int, admin: User = Depends(require_admin), db: Session = Depends(db_session)):
        reply = db.get(Reply, reply_id)
        if not reply or reply.status != "held" or not reply.photo_path or not storage.exists(reply.photo_path):
            raise HTTPException(404)
        return Response(storage.get(reply.photo_path), media_type=content_type(reply.photo_path),
                        headers={"Cache-Control": "private, no-store"})

    @app.post("/admin/run", dependencies=[Depends(check_csrf)])
    def run_now(request: Request, admin: User = Depends(require_admin), db: Session = Depends(db_session)):
        summary = jobs.run_due(db, settings, app.state.services)
        if summary.skipped:
            flash(request, "Another run is already in progress. Try again in a few minutes.")
            return RedirectResponse("/admin", status_code=303)
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

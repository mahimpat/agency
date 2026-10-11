"""Transactional email over SMTP. Without SMTP settings, emails are printed to the log."""

import logging
import smtplib
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger("letters.email")


def send_email(settings: Settings, to: str, subject: str, body: str) -> bool:
    """Returns False if the email couldn't be sent (the error is logged). Without SMTP set up
    (development), the email goes to the log and counts as sent."""
    if not settings.smtp_host:
        log.warning("Email (SMTP not configured) to=%s subject=%s\n%s", to, subject, body)
        return True
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError):
        log.exception("Could not send email to %s", to)
        return False
    return True


def send_marketing_email(settings: Settings, user, subject: str, body: str) -> bool:
    """News and offers: only to parents who opted in, always with a one-click unsubscribe link."""
    if not user.marketing_opt_in:
        return False
    from .security import make_unsubscribe_token
    link = f"{settings.base_url}/unsubscribe/{make_unsubscribe_token(settings.secret_key, user.id)}"
    send_email(settings, user.email, subject,
               f"{body}\n\n--\nYou're getting this because you asked for news from Letters from History.\n"
               f"Unsubscribe: {link}\n(Service emails about your letters will still be sent.)")
    return True

"""Transactional email over SMTP. Without SMTP settings, emails are printed to the log."""

import logging
import smtplib
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger("letters.email")


def send_email(settings: Settings, to: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        log.warning("Email (SMTP not configured) to=%s subject=%s\n%s", to, subject, body)
        return
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

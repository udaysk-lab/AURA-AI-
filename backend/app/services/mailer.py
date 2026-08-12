"""Outbound email.

Two routes, tried in order: the user's connected Gmail account, then SMTP. If
neither is available the caller is told plainly that nothing was sent — the one
outcome that is never acceptable here is reporting success for an email that
didn't leave the building.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from sqlalchemy.orm import Session

from app.config import settings
from app.models import User
from app.services import google as google_service
from app.services import vault as vault_service

log = logging.getLogger("aura.mailer")


def available_routes(db: Session, user: User) -> list[str]:
    routes = []
    if google_service.is_connected(db, user.id):
        routes.append("gmail")
    if settings.smtp_configured:
        routes.append("smtp")
    return routes


def _send_smtp(sender: str, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = formataddr(("", sender))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=20) as server:
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.ehlo()
        if settings.smtp_use_tls:
            server.starttls(context=context)
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


def send(db: Session, user: User, to: str, subject: str, body: str) -> dict:
    """Send an email. Returns a result the agent can report truthfully."""
    _, address = parseaddr(to)
    if not address or "@" not in address:
        return {"sent": False, "reason": "invalid_recipient", "to": to}

    # Resolve any {{vault:…}} references at the very last moment. This is the
    # only place the plaintext exists, and it goes straight out.
    subject = vault_service.resolve(db, user.id, subject)
    body = vault_service.resolve(db, user.id, body)

    if google_service.is_connected(db, user.id):
        try:
            result = google_service.send_email(db, user, address, subject, body)
            if result.get("sent"):
                return {**result, "route": "gmail"}
            log.warning("Gmail declined the send; falling back to SMTP if configured.")
        except Exception as exc:
            log.warning("Gmail send failed (%s); trying SMTP.", exc)

    if settings.smtp_configured:
        sender = settings.smtp_from or user.email
        try:
            _send_smtp(sender, address, subject, body)
            return {"sent": True, "route": "smtp", "to": address, "subject": subject}
        except Exception as exc:
            log.exception("SMTP send failed")
            return {
                "sent": False,
                "reason": "smtp_error",
                "error": str(exc)[:300],
                "to": address,
                "subject": subject,
            }

    return {
        "sent": False,
        "reason": "no_route",
        "to": address,
        "subject": subject,
        "draft_preserved": True,
    }

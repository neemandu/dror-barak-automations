"""Sending email.

One place that actually puts mail on the wire, so the automations describe what
they want sent rather than each speaking SMTP.

Email is the channel that kept the promise WhatsApp couldn't: Dror can reword
anything in :mod:`src.lib.email_templates` and it goes out changed, with no Meta
approval and no deploy.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, NamedTuple, Optional

from . import config, email_templates
from .logging_setup import get_logger

log = get_logger("email", "send")


class Attachment(NamedTuple):
    filename: str
    content: bytes
    mime_type: str = "application/pdf"


class EmailError(RuntimeError):
    """Raised when a message could not be sent."""


def _settings() -> dict[str, Any]:
    missing = [k for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD") if not config.get(k)]
    if missing:
        raise EmailError(
            f"Email is not configured: {', '.join(missing)} not set. "
            f"For Gmail/Workspace use an App Password — a normal account password "
            f"will not authenticate. See docs/CREDENTIALS.md."
        )
    return {
        "host": config.require("SMTP_HOST"),
        "port": int(config.get("SMTP_PORT", "587")),
        "user": config.require("SMTP_USER"),
        "password": config.require("SMTP_PASSWORD"),
        "sender": config.get("SMTP_FROM") or config.require("SMTP_USER"),
    }


def send(
    to: str,
    subject: str,
    html: str,
    text: str,
    *,
    attachments: Optional[list[Attachment]] = None,
    reply_to: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send one message. Returns a description of what was sent."""
    if not to:
        raise EmailError("no recipient address")

    attachments = attachments or []
    if dry_run:
        log.info("would_send_email", extra={
            "to": to, "subject": subject,
            "attachments": [a.filename for a in attachments],
        })
        return {"sent": False, "dry_run": True, "to": to, "subject": subject,
                "attachments": [a.filename for a in attachments]}

    cfg = _settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    for a in attachments:
        maintype, _, subtype = a.mime_type.partition("/")
        msg.add_attachment(a.content, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=a.filename)

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError(
            f"SMTP rejected the login for {cfg['user']}. For Gmail/Workspace this "
            f"almost always means an App Password is needed rather than the account "
            f"password. ({exc.smtp_code})"
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"could not send to {to}: {exc}") from exc

    log.info("email_sent", extra={"to": to, "subject": subject,
                                  "attachments": [a.filename for a in attachments]})
    return {"sent": True, "to": to, "subject": subject,
            "attachments": [a.filename for a in attachments]}


def send_template(
    name: str,
    to: str,
    *,
    attachments: Optional[list[Attachment]] = None,
    dry_run: bool = False,
    **params: Any,
) -> dict[str, Any]:
    """Render a template from :mod:`email_templates` and send it."""
    rendered = email_templates.render(name, **params)
    return send(
        to,
        rendered["subject"],
        rendered["html"],
        rendered["text"],
        attachments=attachments,
        reply_to=config.get("DROR_EMAIL"),
        dry_run=dry_run,
    )

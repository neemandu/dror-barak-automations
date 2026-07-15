"""T10b — Daily report to Dror by email.

Trigger: scheduled at end of day.

Why email and not WhatsApp: on the official Meta API a business-initiated message
outside the 24-hour window must be a Meta-approved template, and each one is billed
per conversation. Dror's own digest would need its own approved template, would be
billed every single day, and could not change wording without re-approval. Email has
none of those limits, so client-facing messages stay on WhatsApp and Dror's digest
moves here.

The report groups the day's run-log by subject — the same grouping the dashboard
uses (:mod:`src.lib.subjects`), so the two never tell different stories — and leads
with anything that failed.

Manual/dry-run:
    python -m src.automations.daily_email --dry-run
"""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime, time, timezone
from email.message import EmailMessage
from typing import Any, Optional

from ..lib import config, run_log, subjects
from .base import Automation, build_arg_parser, run_cli

NAME = "daily_email"


def _start_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


def _esc(value: Any) -> str:
    from html import escape

    return escape("" if value is None else str(value), quote=True)


def build_html(entries: list[dict[str, Any]], date: str, dashboard_url: str = "") -> str:
    """Render the day's entries as an RTL Hebrew email.

    Inline styles only — mail clients strip <style> blocks and have no dark mode,
    so this deliberately does not share the dashboard's stylesheet.
    """
    counts = subjects.counts(entries)
    if not entries:
        inner = '<p style="color:#6b7280">אין פעילות אוטומציות היום.</p>'
    else:
        inner = ""
        failed = subjects.failures(entries)
        if failed:
            rows = "".join(
                f'<tr><td style="padding:4px 0;color:#c0271c">✕</td>'
                f'<td style="padding:4px 8px"><b>{_esc(e.get("action"))}</b>'
                f'<div style="color:#6b7280;font-size:13px">{_esc(e.get("client_id") or "")} — {_esc(e.get("detail"))}</div></td></tr>'
                for e in failed[:10]
            )
            inner += (
                '<div style="border:1px solid #c0271c;border-radius:8px;padding:12px;margin-bottom:16px">'
                f'<div style="color:#c0271c;font-weight:700;margin-bottom:6px">⚠️ דורש טיפול ({len(failed)})</div>'
                f"<table>{rows}</table></div>"
            )

        for subject, group in subjects.group_by_subject(entries):
            rows = ""
            for e in sorted(group, key=lambda x: str(x.get("ts")), reverse=True):
                mark = {"ok": "✓", "error": "✕", "skipped": "–"}.get(str(e.get("status")), "•")
                colour = {"ok": "#0a7c42", "error": "#c0271c", "skipped": "#8a6d16"}.get(
                    str(e.get("status")), "#6b7280"
                )
                links = "".join(
                    f' <a href="{_esc(u)}" style="color:#1a56db">{_esc(lbl)} ↗</a>'
                    for lbl, u in subjects.links_for(e)
                )
                detail = e.get("detail")
                note = (
                    f'<div style="color:#6b7280;font-size:13px">{_esc(detail)}</div>'
                    if detail and not str(detail).startswith("http")
                    else ""
                )
                rows += (
                    f'<tr><td style="padding:4px 0;color:{colour};width:16px">{mark}</td>'
                    f'<td style="padding:4px 8px;color:#6b7280;font-size:13px;white-space:nowrap">'
                    f'{_esc(subjects.parse_ts(e) or "")}</td>'
                    f'<td style="padding:4px 8px"><b>{_esc(e.get("action"))}</b>{links}{note}</td>'
                    f'<td style="padding:4px 8px;color:#6b7280;font-size:13px">{_esc(e.get("client_id") or "")}</td></tr>'
                )
            inner += (
                '<div style="border:1px solid #e3e6ea;border-radius:8px;padding:12px;margin-bottom:12px">'
                f'<div style="font-weight:700;margin-bottom:6px">{subject.icon} {_esc(subject.label)} '
                f'<span style="color:#6b7280;font-weight:400">({len(group)})</span></div>'
                f'<table style="width:100%;border-collapse:collapse">{rows}</table></div>'
            )

    link = (
        f'<p><a href="{_esc(dashboard_url)}" style="color:#1a56db">פתח את לוח הבקרה ↗</a></p>'
        if dashboard_url
        else ""
    )
    return f"""<div dir="rtl" style="font-family:system-ui,'Segoe UI',Arial,sans-serif;
      max-width:720px;margin:0 auto;padding:16px;color:#1a1d21">
      <h2 style="margin:0 0 2px">סיכום יומי — {_esc(date)}</h2>
      <p style="color:#6b7280;margin:0 0 16px">
        {counts['total']} פעולות · {counts['ok']} הצליחו · {counts['error']} שגיאות
        · {counts['skipped']} דילוגים</p>
      {inner}{link}</div>"""


def build_text(entries: list[dict[str, Any]], date: str) -> str:
    """Plain-text alternative, for clients that refuse HTML."""
    counts = subjects.counts(entries)
    lines = [f"סיכום יומי — {date}", f"{counts['total']} פעולות, {counts['error']} שגיאות", ""]
    for subject, group in subjects.group_by_subject(entries):
        lines.append(f"{subject.label} ({len(group)}):")
        for e in group:
            mark = {"ok": "v", "error": "x", "skipped": "-"}.get(str(e.get("status")), ".")
            lines.append(f"  [{mark}] {e.get('action')} {e.get('client_id') or ''}")
        lines.append("")
    return "\n".join(lines)


def _send_email(subject: str, html_body: str, text_body: str) -> dict[str, Any]:
    """Send via SMTP. Returns a description of what was sent."""
    host = config.require("SMTP_HOST")
    port = int(config.get("SMTP_PORT", "587"))
    user = config.require("SMTP_USER")
    password = config.require("SMTP_PASSWORD")
    sender = config.get("SMTP_FROM", user)
    to = config.require("DROR_EMAIL")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)
    return {"to": to, "subject": subject}


def run(*, dry_run: bool = False, since: Optional[datetime] = None) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    since = since or _start_of_today_utc()
    entries = run_log.read_since(since)
    date = since.date().isoformat()

    dashboard_url = config.get("DASHBOARD_URL", "")
    html_body = build_html(entries, date, dashboard_url)
    text_body = build_text(entries, date)
    counts = subjects.counts(entries)
    subject_line = f"סיכום יומי — {date} · {counts['total']} פעולות"
    if counts["error"]:
        subject_line += f" · ⚠️ {counts['error']} שגיאות"

    if dry_run:
        auto.log_action(
            "email_prepared", client_id=None, detail=f"{len(entries)} entries (dry-run)"
        )
        return {"sent": False, "subject": subject_line, "html": html_body, "entries": len(entries)}

    if not config.get("DROR_EMAIL"):
        auto.log_action("no_recipient", "skipped", detail="DROR_EMAIL not set")
        return {"sent": False, "reason": "DROR_EMAIL not set", "entries": len(entries)}

    sent = _send_email(subject_line, html_body, text_body)
    auto.log_action("email_sent", detail=f"{len(entries)} entries → {sent['to']}")
    return {"sent": True, "subject": subject_line, "entries": len(entries), **sent}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    run_cli(parser, lambda a: run(dry_run=a.dry_run))


if __name__ == "__main__":
    main()

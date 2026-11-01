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

from datetime import datetime, time, timezone
from typing import Any, Optional

from ..lib import config, email_templates, emails, run_log, subjects
from .base import Automation, build_arg_parser, run_cli

NAME = "daily_email"


def _start_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


def _esc(value: Any) -> str:
    from html import escape

    return escape("" if value is None else str(value), quote=True)


# One column, every line free to wrap: a phone is 360 px wide, and a row of
# time | name | client | detail cells does not fit in it.
_WRAP = "overflow-wrap:anywhere;word-break:break-word"
_MUTED = f"color:#6b7280;font-size:13px;line-height:1.5;{_WRAP}"


def _entry_row(e: dict[str, Any], names: dict[str, str], *, with_time: bool = True) -> str:
    status = str(e.get("status"))
    mark = {"ok": "✓", "error": "✕", "skipped": "-"}.get(status, "•")
    colour = {"ok": "#0a7c42", "error": "#c0271c", "skipped": "#8a6d16"}.get(status, "#6b7280")
    links = "".join(
        f' <a href="{_esc(u)}" style="color:#1a56db;white-space:nowrap">{_esc(lbl)} ↗</a>'
        for lbl, u in subjects.links_for(e)
    )
    who = str(e.get("client_id") or "")
    detail = e.get("detail")
    meta = [
        subjects.parse_ts(e) if with_time else "",
        names.get(who, who),
        "" if not detail or str(detail).startswith("http") else str(detail),
    ]
    note = " · ".join(_esc(m) for m in meta if m)
    note_html = f'<div style="{_MUTED}">{note}</div>' if note else ""
    return (
        f'<tr><td valign="top" style="padding:6px 0 6px 8px;color:{colour};width:14px">{mark}</td>'
        f'<td style="padding:6px 0;{_WRAP}"><b>{_esc(subjects.label_for(e))}</b>{links}{note_html}</td></tr>'
    )


def build_html(entries: list[dict[str, Any]], date: str, dashboard_url: str = "",
               names: Optional[dict[str, str]] = None) -> str:
    """Render the day's entries as an RTL Hebrew email.

    Inline styles only — mail clients strip <style> blocks and have no dark mode,
    so this deliberately does not share the dashboard's stylesheet. ``names`` maps
    a client id (a ClickUp task id) to the client's name.
    """
    names = names or {}
    table = 'role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse"'
    counts = subjects.counts(entries)
    if not entries:
        inner = '<p style="color:#6b7280">אין פעילות אוטומציות היום.</p>'
    else:
        inner = ""
        failed = subjects.failures(entries)
        if failed:
            rows = "".join(_entry_row(e, names, with_time=False) for e in failed[:10])
            inner += (
                '<div style="border:1px solid #c0271c;border-radius:8px;padding:10px 12px;margin-bottom:16px">'
                f'<div style="color:#c0271c;font-weight:700;margin-bottom:4px">⚠️ דורש טיפול ({len(failed)})</div>'
                f"<table {table}>{rows}</table></div>"
            )

        for subject, group in subjects.group_by_subject(entries):
            rows = "".join(_entry_row(e, names)
                           for e in sorted(group, key=lambda x: str(x.get("ts")), reverse=True))
            inner += (
                '<div style="border:1px solid #e3e6ea;border-radius:8px;padding:10px 12px;margin-bottom:12px">'
                f'<div style="font-weight:700;margin-bottom:4px">{subject.icon} {_esc(subject.label)} '
                f'<span style="color:#6b7280;font-weight:400">({len(group)})</span></div>'
                f'<table {table}>{rows}</table></div>'
            )

    link = (
        f'<p><a href="{_esc(dashboard_url)}" style="color:#1a56db">פתח את לוח הבקרה ↗</a></p>'
        if dashboard_url
        else ""
    )
    return email_templates.layout(
        f'<h2 style="margin:0 0 2px;font-size:20px">סיכום יומי, {_esc(date)}</h2>'
        f'<p style="color:#6b7280;margin:0 0 16px;font-size:14px">'
        f"{counts['total']} פעולות · {counts['ok']} הצליחו · {counts['error']} שגיאות"
        f" · {counts['skipped']} דילוגים</p>{inner}{link}",
        internal=True, width=680,
        preheader=f"{counts['total']} פעולות, {counts['error']} שגיאות")


def _client_names(dry_run: bool) -> dict[str, str]:
    """Client names from ClickUp, or {} (the ids then show as they are)."""
    from ..clients_pages import _names

    return _names(dry_run)[0]


def build_text(entries: list[dict[str, Any]], date: str) -> str:
    """Plain-text alternative, for clients that refuse HTML."""
    counts = subjects.counts(entries)
    lines = [f"סיכום יומי, {date}", f"{counts['total']} פעולות, {counts['error']} שגיאות", ""]
    for subject, group in subjects.group_by_subject(entries):
        lines.append(f"{subject.label} ({len(group)}):")
        for e in group:
            mark = {"ok": "v", "error": "x", "skipped": "-"}.get(str(e.get("status")), ".")
            lines.append(f"  [{mark}] {subjects.label_for(e)} {e.get('client_id') or ''}")
        lines.append("")
    return "\n".join(lines)


def run(*, dry_run: bool = False, since: Optional[datetime] = None) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    since = since or _start_of_today_utc()
    entries = run_log.read_since(since)
    date = since.date().isoformat()

    dashboard_url = config.get("DASHBOARD_URL", "")
    html_body = build_html(entries, date, dashboard_url, _client_names(dry_run))
    text_body = build_text(entries, date)
    counts = subjects.counts(entries)
    subject_line = f"סיכום יומי {date}: {counts['total']} פעולות"
    if counts["error"]:
        subject_line += f", {counts['error']} שגיאות"

    if dry_run:
        auto.log_action(
            "email_prepared", client_id=None, detail=f"{len(entries)} entries (dry-run)"
        )
        return {"sent": False, "subject": subject_line, "html": html_body, "entries": len(entries)}

    to = config.get("DROR_EMAIL")
    if not to:
        auto.log_action("no_recipient", "skipped", detail="DROR_EMAIL not set")
        return {"sent": False, "reason": "DROR_EMAIL not set", "entries": len(entries)}

    # The shared sender, so a missing App Password fails with the same hint here
    # as everywhere else rather than a bare SMTP error.
    sent = emails.send(to, subject_line, html_body, text_body,
                       inline=email_templates.inline_images())
    auto.log_action("email_sent", detail=f"{len(entries)} entries → {sent['to']}")
    return {"sent": True, "subject": subject_line, "entries": len(entries), **sent}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    run_cli(parser, lambda a: run(dry_run=a.dry_run))


if __name__ == "__main__":
    main()

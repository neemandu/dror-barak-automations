"""Grouping run-log entries into the subjects Dror thinks in.

The run-log records one entry per *action* (``drive_folder_created``,
``payment_requested``, ...). Dror does not think in actions — he thinks in
"what's happening with the invoices / the leads / the campaign reports". This
module maps entries onto those subjects and pulls out any link an entry carries,
so the dashboard and the daily email can both present the log the way he reads it.

Action rules win over automation rules, because a single automation can touch
several subjects: onboarding creates a Drive folder *and* a Morning client, and
those belong under different headings.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple, Optional


class Subject(NamedTuple):
    key: str
    label: str  # Hebrew — this is Dror-facing
    icon: str


SUBJECTS: dict[str, Subject] = {
    "clickup": Subject("clickup", "לידים ומשימות", "📋"),
    "quotes": Subject("quotes", "הצעות מחיר וחתימות", "✍️"),
    "morning": Subject("morning", "חשבוניות ותשלומים", "💰"),
    "meta": Subject("meta", "קמפיינים ודוחות", "📊"),
    "whatsapp": Subject("whatsapp", "הודעות ללקוחות", "💬"),
    "drive": Subject("drive", "קבצים ותיקיות", "📁"),
    "ai": Subject("ai", "ניתוח ואסטרטגיה", "🤖"),
    "system": Subject("system", "מערכת", "⚙️"),
}

UNKNOWN = Subject("other", "אחר", "•")

# Checked in order; first substring match on the action name wins.
_ACTION_RULES: tuple[tuple[str, str], ...] = (
    ("drive", "drive"),
    ("folder", "drive"),
    ("template", "drive"),
    ("morning", "morning"),
    ("payment", "morning"),
    ("invoice", "morning"),
    ("quote", "quotes"),
    ("signature", "quotes"),
    ("signed", "quotes"),
    ("contract", "quotes"),
    ("whatsapp", "whatsapp"),
    ("message", "whatsapp"),
    ("questionnaire", "whatsapp"),
    ("campaign", "meta"),
    ("task", "clickup"),
    ("lead", "clickup"),
    ("contact", "clickup"),
    ("strategy", "ai"),
    ("prep", "ai"),
    ("analysis", "ai"),
    ("report", "ai"),
)

_AUTOMATION_RULES: dict[str, str] = {
    "lead_to_contacts": "clickup",
    "clickup_to_claude": "clickup",
    "send_questionnaire": "whatsapp",
    "send_quote": "quotes",
    "onboarding": "drive",
    "monthly_payment_requests": "morning",
    "campaign_summary": "meta",
    "social_prep": "ai",
    "strategy_bot": "ai",
    "daily_summary": "system",
    "daily_email": "system",
    "migrate_taskey_to_clickup": "system",
}


def subject_for(entry: dict[str, Any]) -> Subject:
    """Return the subject an entry belongs under."""
    action = str(entry.get("action") or "").lower()
    for needle, key in _ACTION_RULES:
        if needle in action:
            return SUBJECTS[key]
    automation = str(entry.get("automation") or "")
    key = _AUTOMATION_RULES.get(automation)
    return SUBJECTS[key] if key else UNKNOWN


_URL_RE = re.compile(r"https?://[^\s,;'\"<>)\]]+")

# Field names an automation may use to carry a link, most explicit first.
_LINK_FIELDS = ("url", "link", "webViewLink", "web_view_link", "doc", "detail")

_LINK_LABELS: tuple[tuple[str, str], ...] = (
    ("drive.google.com", "פתח בדרייב"),
    ("docs.google.com", "פתח מסמך"),
    ("app.clickup.com", "פתח ב-ClickUp"),
    ("clickup.com", "פתח ב-ClickUp"),
    ("greeninvoice", "פתח ב-Morning"),
    ("facebook.com", "פתח ב-Meta"),
)


def links_for(entry: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ``(label, url)`` pairs found on an entry, de-duplicated.

    Automations currently put links wherever is convenient — most often inside the
    free-text ``detail`` — so rather than trust one field we scan the known link
    fields and pick out anything URL-shaped. New code should pass ``url=`` to
    ``log_action`` explicitly; this keeps older entries clickable regardless.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field in _LINK_FIELDS:
        value = entry.get(field)
        if not isinstance(value, str):
            continue
        for url in _URL_RE.findall(value):
            if url in seen:
                continue
            seen.add(url)
            out.append((_label_for(url), url))
    return out


def _label_for(url: str) -> str:
    lowered = url.lower()
    for needle, label in _LINK_LABELS:
        if needle in lowered:
            return label
    return "פתח קישור"


def group_by_subject(
    entries: list[dict[str, Any]],
) -> list[tuple[Subject, list[dict[str, Any]]]]:
    """Group entries by subject, ordered as ``SUBJECTS`` is declared.

    Subjects with no entries are omitted — an empty heading tells Dror nothing.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        buckets.setdefault(subject_for(entry).key, []).append(entry)
    order = list(SUBJECTS) + [UNKNOWN.key]
    lookup = dict(SUBJECTS)
    lookup[UNKNOWN.key] = UNKNOWN
    return [(lookup[key], buckets[key]) for key in order if key in buckets]


def failures(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries that need Dror's attention, newest first."""
    bad = [e for e in entries if e.get("status") == "error"]
    return sorted(bad, key=lambda e: str(e.get("ts") or ""), reverse=True)


def client_ids(entries: list[dict[str, Any]]) -> list[str]:
    return sorted({str(e["client_id"]) for e in entries if e.get("client_id")})


def counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Headline numbers: total, ok, errors, skipped, and dry-run."""
    return {
        "total": len(entries),
        "ok": sum(1 for e in entries if e.get("status") == "ok"),
        "error": sum(1 for e in entries if e.get("status") == "error"),
        "skipped": sum(1 for e in entries if e.get("status") == "skipped"),
        "dry_run": sum(1 for e in entries if e.get("dry_run")),
    }


def parse_ts(entry: dict[str, Any]) -> Optional[str]:
    """``HH:MM`` for display, or ``None`` if the timestamp is unusable."""
    ts = entry.get("ts")
    if not isinstance(ts, str) or "T" not in ts:
        return None
    return ts.split("T", 1)[1][:5]

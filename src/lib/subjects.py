"""Grouping run-log entries into the subjects Dror thinks in.

The run-log records one entry per *action* (``drive_folder_created``,
``payment_requested``, ...). Dror does not think in actions — he thinks in
"what's happening with the leads / the contracts / the campaign reports". This
module maps entries onto those subjects and pulls out any link an entry carries,
so the dashboard and the daily email can both present the log the way he reads it.

Action rules win over automation rules, because a single automation can touch
several subjects: onboarding creates a Drive folder *and* sends the questionnaire,
and those belong under different headings.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple, Optional


class Subject(NamedTuple):
    key: str
    label: str  # Hebrew — this is Dror-facing
    icon: str


SUBJECTS: dict[str, Subject] = {
    "leads": Subject("leads", "לידים חדשים", "🙋"),
    "clickup": Subject("clickup", "לידים ומשימות", "📋"),
    "quotes": Subject("quotes", "הצעות מחיר וחתימות", "✍️"),
    "meta": Subject("meta", "קמפיינים ודוחות", "📊"),
    "whatsapp": Subject("whatsapp", "הודעות ללקוחות", "💬"),
    "drive": Subject("drive", "קבצים ותיקיות", "📁"),
    "ai": Subject("ai", "ניתוח ואסטרטגיה", "🤖"),
    "system": Subject("system", "מערכת", "⚙️"),
}

UNKNOWN = Subject("other", "אחר", "•")

#: What each logged action means, in the words Dror would use. The run-log keeps
#: the stable English names; the dashboard and the daily email show these.
ACTION_LABELS: dict[str, str] = {
    # leads and tasks
    "contact_saved": "הליד נשמר באנשי הקשר",
    "no_phone": "לליד אין מספר טלפון",
    "flow_sent": "הודעת וואטסאפ נשלחה לליד",
    "unknown_msg": "הודעה לא מוכרת מ-Smoove",
    "draft_posted": "Claude השלים משימה",
    "draft_revised": "Claude תיקן לפי ההערה",
    "draft_failed": "Claude לא הצליח להשלים משימה",
    "pdf_attach_failed": "לא הצלחנו לצרף את ה-PDF למשימה",
    "status_not_set": "לא הצלחנו לעדכן את סטטוס המשימה",
    "drive_doc_created": "Claude יצר מסמך ב-Drive",
    "gmail_draft_created": "Claude הכין טיוטת מייל",
    "client_email_sent": "מייל נשלח באישורך",
    "send_refused": "סירבנו לשלוח מייל: האישור לא היה שלך",
    # quotes and signing
    "quote_sent": "הצעת המחיר נשלחה לחתימה",
    "no_price": "חסר מחיר להצעה",
    "signed": "ההסכם נחתם",
    "reminder_sent": "נשלחה תזכורת ללקוח",
    "reminder_failed": "תזכורת לא נשלחה",
    "reminder_cancelled": "התזכורת בוטלה (המשימה נסגרה)",
    "reminder_task_failed": "משימת התזכורת לא נפתחה ב-ClickUp",
    "reminders_done": "סבב התזכורות הסתיים",
    "no_pending_record": "אין חתימה שממתינה",
    # questionnaire
    "questionnaire_sent": "השאלון נשלח ללקוח",
    "questionnaire_send_failed": "שליחת השאלון נכשלה",
    "questionnaire_answered": "הלקוח מילא את השאלון",
    "questionnaire_doc_failed": "מסמך התשובות לא נוצר",
    "questionnaire_unanswered": "השאלון עדיין לא מולא",
    "questionnaire_reminders_done": "סבב התזכורות לשאלון הסתיים",
    "no_email": "אין ללקוח כתובת מייל",
    # onboarding and Drive
    "drive_folder_created": "נפתחה תיקיית לקוח",
    "drive_folder_reused": "תיקיית הלקוח כבר קיימת",
    "subfolders_created": "נוצרו תיקיות משנה",
    "subfolders_failed": "יצירת תיקיות המשנה נכשלה",
    "templates_copied": "התבניות הועתקו לתיקייה",
    "template_copied": "תבנית הועתקה לתיקיית הלקוח",
    "template_missing": "לא נמצאה תבנית בשם שנבחר",
    "folder_shared": "תיקיית הלקוח שותפה עם הלקוח",
    "folder_not_shared": "התיקייה לא שותפה: אין ללקוח מייל",
    "folder_share_failed": "שיתוף התיקייה עם הלקוח נכשל",
    "template_copy_failed": "העתקת תבנית נכשלה",
    "no_templates": "לא הוגדרו תבניות להעתקה",
    "folder_listing_failed": "קריאת תיקיית הלקוח נכשלה",
    "welcome_flow_sent": "נשלחה הודעת ברוכים הבאים",
    "welcome_flow_failed": "הודעת ברוכים הבאים לא נשלחה",
    "no_welcome_flow": "אין עדיין הודעת ברוכים הבאים מאושרת",
    "no_phone_for_welcome": "אין טלפון להודעת ברוכים הבאים",
    "meta_account_present": "חשבון המודעות ב-Meta מחובר",
    "meta_account_missing": "חסר חשבון מודעות ב-Meta",
    "onboarding_done": "האונבורדינג הושלם",
    "summary_comment_failed": "סיכום האונבורדינג לא נכתב במשימה",
    # AI
    "prep_report_ready": "דוח ההכנה לרשתות מוכן",
    "no_profiles": "אין קישורים לרשתות בשאלון",
    "strategy_ready": "טיוטת האסטרטגיה מוכנה",
    "no_questionnaire": "אין תשובות לשאלון, האסטרטגיה לא נבנתה",
    "dror_notified": "נשלח אליך מייל",
    "dror_not_notified": "לא נשלח אליך מייל",
    "notify_failed": "המייל אליך נכשל",
    "task_failed": "משימה ברקע נכשלה",
    # campaigns
    "campaign_summary_ready": "דוח הקמפיינים מוכן לאישור",
    "campaign_report_built": "דוח הקמפיינים נבנה",
    "campaign_reports_done": "סבב דוחות הקמפיינים הסתיים",
    "no_ad_account": "אין ללקוח חשבון מודעות",
    "report_failed": "דוח הקמפיינים נכשל",
    "approval_email_failed": "מייל האישור לדוח נכשל",
    # system
    "email_sent": "הסיכום היומי נשלח",
    "email_prepared": "הסיכום היומי הוכן",
    "email_failed": "שליחת מייל נכשלה",
    "no_recipient": "אין נמען לסיכום היומי",
    "summary_sent": "הסיכום היומי נשלח",
    "migration_done": "ההעברה מ-Taskey הסתיימה",
    "task_created": "משימה נוצרה ב-ClickUp",
    "task_error": "יצירת משימה נכשלה",
    "mapping_resolved": "מיפוי השדות הושלם",
    "limit_reached": "הגענו למגבלה",
}


def label_for(entry: dict[str, Any]) -> str:
    """The Hebrew line for an entry's action (the raw name if it has none)."""
    action = str(entry.get("action") or "")
    return ACTION_LABELS.get(action) or action.replace("_", " ")

# Checked in order; first substring match on the action name wins.
_ACTION_RULES: tuple[tuple[str, str], ...] = (
    ("drive", "drive"),
    ("folder", "drive"),
    ("template", "drive"),
    ("quote", "quotes"),
    ("signature", "quotes"),
    ("signed", "quotes"),
    ("contract", "quotes"),
    ("whatsapp", "whatsapp"),
    ("message", "whatsapp"),
    ("questionnaire", "whatsapp"),
    ("campaign", "meta"),
    # Onboarding's ad-account check: a Meta matter, not a Drive one.
    ("meta_account", "meta"),
    ("task", "clickup"),
    ("lead", "clickup"),
    ("contact", "clickup"),
    ("strategy", "ai"),
    ("prep", "ai"),
    ("analysis", "ai"),
    ("report", "ai"),
)

_AUTOMATION_RULES: dict[str, str] = {
    # Smoove signups: people who are not in ClickUp (yet), only in ManyChat.
    "smoove_to_manychat": "leads",
    "lead_to_contacts": "clickup",
    "clickup_to_claude": "clickup",
    "send_questionnaire": "whatsapp",
    "send_quote": "quotes",
    "sign_contract": "quotes",
    "file_contract": "quotes",
    "onboarding": "drive",
    # Both chase jobs log a bare `reminder_sent`, which no action rule matches.
    "sign_reminders": "quotes",
    "questionnaire_reminders": "whatsapp",
    "campaign_summary": "meta",
    "social_prep": "ai",
    "strategy_bot": "ai",
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


def is_lead(entry: dict[str, Any]) -> bool:
    """A Smoove signup: its ``client_id`` is a phone number, not a ClickUp client."""
    return entry.get("automation") == "smoove_to_manychat"


def phone_display(phone: Any) -> str:
    """``+972525525300`` → ``052-5525300``; anything else as it came."""
    raw = str(phone or "")
    if raw.startswith("+972") and len(raw) == 13:
        return f"0{raw[4:6]}-{raw[6:]}"
    return raw


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

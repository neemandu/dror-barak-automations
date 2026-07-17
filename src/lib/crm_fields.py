"""Mapping between Dror's client model and whatever ClickUp actually contains.

ClickUp custom fields **cannot be created through the API** — the public API only
reads fields and sets values on fields that already exist. So the clients list is
built by hand in the ClickUp UI, and this module's job is to recognise what was
built: it matches fields, statuses and dropdown options by *name*, in Hebrew or
English, rather than by ids pasted into ``.env``.

That matters practically: the ids change if the list is ever rebuilt, and nobody
has to copy them anywhere. It also means a renamed field degrades to "field not
found" (visible via ``python -m src.tools.check_clickup_crm``) instead of writing
to the wrong column.

Shared by the CRM client (:mod:`src.lib.clients.crm`) and the Taskey migration
(:mod:`src.tools.migrate_taskey_to_clickup`) so the two cannot drift apart.
"""

from __future__ import annotations

from typing import Any, Optional

# Canonical client field -> names it may carry in ClickUp / a Taskey export.
ALIASES: dict[str, list[str]] = {
    "name": ["name", "client", "לקוח", "שם", "שם לקוח", "שם הלקוח"],
    "phone": ["phone", "mobile", "טלפון", "נייד", "מספר טלפון"],
    "email": ["email", "mail", "מייל", "אימייל", 'דוא"ל', "דואל"],
    # "סטטוס"/"status" on its own is ambiguous — it has been used for both the
    # primary and the secondary status. Dropdowns are classified by their options
    # instead (see classify_dropdown), which is decisive; these names are only the
    # fallback for non-dropdown fields.
    "status": ["סטטוס ראשי", "primary status"],
    "sub_status": ["sub status", "substatus", "סטטוס משני"],
    "monthly_price": [
        "monthly price", "price", "מחיר חודשי", "מחיר", "ריטיינר",
        # Dror's field is explicitly ex-VAT. Morning adds מע"מ when it issues the
        # document, so an ex-VAT amount is the correct thing to send it.
        "מחיר חודשי ללא מעמ", 'מחיר חודשי ללא מע"מ', "מחיר ללא מעמ",
    ],
    "service_type": ["service type", "service", "סוג שירות"],
    "drive_folder": [
        "drive", "drive folder", "נתיב תיקיית drive", "תיקיית drive", "דרייב",
        "נתיב לגוגל דרייב", "תיקיית גוגל דרייב", "גוגל דרייב",
    ],
    "signed_contract": ["signed contract", "contract", "חוזה חתום", "חוזה"],
    "recordings_path": ["recordings", "נתיב הקלטות", "הקלטות"],
    "morning_status": ["morning", "morning status", "סטטוס morning", "סטטוס מורנינג"],
    "morning_client_id": [
        "morning client id", "morning id", "מזהה morning", "מזהה מורנינג",
    ],
    # The Meta ad account for the monthly campaign report (T7), the act_ id.
    # NOTE: normalize() casefolds, so a "Meta" the user typed arrives as "meta" —
    # every alias here must be lowercase or it silently never matches, and the
    # failure looks like a config problem rather than a code bug.
    "meta_ad_account": [
        "meta", "meta ad account", "ad account", "act id",
        "חשבון מודעות meta", "חשבון מודעות", "מזהה חשבון מודעות",
    ],
}

NUMERIC_FIELDS = {"monthly_price"}

# ClickUp field types that hold a number. 'currency' is what a price field
# naturally becomes in the UI, and it returns a plain number.
NUMERIC_TYPES = {"number", "currency"}

# Primary status — the ClickUp task status.
STATUS_LEAD = "lead"
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_FINISHED = "finished"

# Secondary status — a dropdown custom field.
SUB_INITIAL_MEETING = "initial_meeting"
SUB_QUESTIONNAIRE_SENT = "questionnaire_sent"
SUB_QUOTE_SENT = "quote_sent"
SUB_SIGNED = "signed"
SUB_IN_WORK = "in_work"

STATUS_ALIASES: dict[str, list[str]] = {
    STATUS_LEAD: ["lead", "ליד", "לידים"],
    STATUS_ACTIVE: ["active", "לקוח פעיל", "פעיל"],
    STATUS_PAUSED: ["paused", "on hold", "מושהה", "בהמתנה"],
    STATUS_FINISHED: ["finished", "complete", "done", "closed", "הסתיים", "סיום"],
}

SUB_STATUS_ALIASES: dict[str, list[str]] = {
    SUB_INITIAL_MEETING: [
        "initial meeting", "פגישה ראשונית", "פגישת היכרות", "נעשתה פגישה ראשונית",
    ],
    SUB_QUESTIONNAIRE_SENT: ["questionnaire sent", "נשלח שאלון", "שאלון נשלח"],
    SUB_QUOTE_SENT: ["quote sent", "נשלחה הצעת מחיר", "הצעת מחיר נשלחה"],
    SUB_SIGNED: ["signed", "חתם", "נחתם", "חוזה חתום"],
    SUB_IN_WORK: ["in work", "in progress", "בעבודה"],
}


def normalize(text: str) -> str:
    return " ".join((text or "").strip().casefold().split())


def _match(raw: str, table: dict[str, list[str]]) -> Optional[str]:
    norm = normalize(raw)
    if not norm:
        return None
    for canonical, names in table.items():
        if norm == canonical or norm in names:
            return canonical
    return None


def canonical_for(field_name: str) -> Optional[str]:
    """The canonical field a ClickUp field / CSV column name maps to."""
    return _match(field_name, ALIASES)


def canonical_status(raw: str) -> Optional[str]:
    return _match(raw, STATUS_ALIASES)


def canonical_sub_status(raw: str) -> Optional[str]:
    return _match(raw, SUB_STATUS_ALIASES)


def classify_dropdown(field: dict[str, Any]) -> Optional[str]:
    """Classify a dropdown as ``status`` / ``sub_status`` by its *options*.

    A field's name is a weak signal — "סטטוס" has been used for both the primary
    and the secondary status, and getting it backwards means reading the wrong
    lifecycle entirely. The options are decisive: only the secondary status has an
    option meaning "questionnaire sent".

    Returns ``None`` for any dropdown that isn't clearly one of the two, so
    ordinary dropdowns fall through to name matching.
    """
    if str(field.get("type") or "") != "drop_down":
        return None
    names = [
        str(o.get("name") or "")
        for o in (field.get("type_config") or {}).get("options") or []
    ]
    if not names:
        return None
    sub_hits = sum(1 for n in names if canonical_sub_status(n))
    status_hits = sum(1 for n in names if canonical_status(n))
    # Require a majority so one coincidental option can't flip the answer.
    if sub_hits > status_hits and sub_hits >= len(names) / 2:
        return "sub_status"
    if status_hits > sub_hits and status_hits >= len(names) / 2:
        return "status"
    return None


def resolve_fields(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``{canonical: field}`` for the list's custom fields we recognise.

    Dropdowns are classified by their options first, then everything falls back to
    name matching. Unrecognised fields are ignored — Dror keeps his own columns
    alongside ours.
    """
    resolved: dict[str, dict[str, Any]] = {}
    for field in fields:
        canonical = classify_dropdown(field) or canonical_for(str(field.get("name") or ""))
        if canonical and canonical not in resolved:
            resolved[canonical] = field
    return resolved


def missing_fields(fields: list[dict[str, Any]], required: list[str]) -> list[str]:
    resolved = resolve_fields(fields)
    return [name for name in required if name not in resolved]


def dropdown_option_id(field: dict[str, Any], label: str) -> Optional[str]:
    """The option id for a dropdown label, matched via the alias tables.

    ClickUp dropdowns take an option *id*, never the label, so a canonical value
    like ``signed`` has to be resolved against whatever Dror named the option
    ("חתם", "נחתם", ...).
    """
    options = (field.get("type_config") or {}).get("options") or []
    wanted = normalize(label)
    canonical = canonical_sub_status(label) or canonical_status(label)
    for option in options:
        name = str(option.get("name") or "")
        if normalize(name) == wanted:
            return str(option.get("id"))
        if canonical and (
            canonical_sub_status(name) == canonical or canonical_status(name) == canonical
        ):
            return str(option.get("id"))
    return None


def dropdown_label(field: dict[str, Any], option_value: Any) -> Optional[str]:
    """Reverse of :func:`dropdown_option_id` — the label for a stored value.

    ClickUp returns a dropdown's value as either the option id or its index,
    depending on the endpoint, so both are accepted.
    """
    options = (field.get("type_config") or {}).get("options") or []
    for option in options:
        if str(option.get("id")) == str(option_value):
            return str(option.get("name"))
    if isinstance(option_value, int) and 0 <= option_value < len(options):
        return str(options[option_value].get("name"))
    return None


def coerce_value(field: dict[str, Any], value: Any) -> Any:
    """Convert a Python value into what ClickUp's set-value endpoint expects."""
    ftype = str(field.get("type") or "")
    if ftype == "drop_down":
        option_id = dropdown_option_id(field, str(value))
        # Falling back to the raw value would silently set the wrong option.
        if option_id is None:
            raise ValueError(
                f"no option named {value!r} on dropdown {field.get('name')!r}. "
                f"Options: {[o.get('name') for o in (field.get('type_config') or {}).get('options', [])]}"
            )
        return option_id
    if ftype in NUMERIC_TYPES:
        return float(value) if value not in (None, "") else None
    if ftype == "attachment":
        # Reached only if someone passes a bare string. The real path uploads the
        # file first (CrmClient.attach_file) and sets the field to the resulting
        # attachment id, so a raw string here is a caller mistake.
        raise ValueError(
            f"{field.get('name')!r} is an Attachment field: it holds an uploaded "
            f"file, not a string. Use CrmClient.attach_file(client_id, field, path)."
        )
    return value


def read_value(field: dict[str, Any], raw: Any) -> Any:
    """Convert a value ClickUp returned into a plain Python value."""
    if raw in (None, ""):
        return ""
    ftype = str(field.get("type") or "")
    if ftype == "drop_down":
        return dropdown_label(field, raw) or ""
    if ftype == "attachment":
        # ClickUp returns a list of attachment objects; surface the first URL so a
        # contract uploaded by hand is at least readable.
        if isinstance(raw, list) and raw:
            return str(raw[0].get("url") or "")
        return ""
    return raw

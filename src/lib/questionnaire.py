"""Questionnaires — what they are made of, and what answers mean.

A questionnaire is a plain dict ("definition") that Dror edits in the admin
editor and :mod:`src.lib.questionnaire_store` keeps:

    {"id", "title", "intro", "thanks", "version",
     "sections": [{"id", "title", "description",
                   "questions": [{"key", "label", "kind", "required", "hint",
                                  "options", "role", "scale_max"}]}]}

Two rules keep editing safe:

* **A question's ``key`` never changes.** It is generated when the question is
  created, and answers are stored under it. Rewording a label, moving a question,
  or changing its section cannot orphan an answer or break a lookup.
* **Meaning comes from ``role``, not from wording.** The social-media analysis
  looks for questions whose role is ``instagram``/``tiktok``/…, so Dror can ask
  "איפה אתם באינסטגרם?" instead of "קישור לאינסטגרם" and nothing breaks.

Every submission stores a **snapshot** of the questions it answered, so an old
response still reads correctly after the questionnaire changes.
"""

from __future__ import annotations

import copy
import html
import re
import secrets
from typing import Any, Iterator, Optional

# kind -> Hebrew name shown in the editor.
KINDS: dict[str, str] = {
    "text": "טקסט קצר",
    "textarea": "טקסט ארוך",
    "email": "אימייל",
    "tel": "טלפון",
    "url": "קישור",
    "number": "מספר",
    "choice": "בחירה אחת",
    "multi": "בחירה מרובה",
    "scale": "סולם דירוג",
}

# role -> Hebrew name. Only links have roles: these are what the AI reads.
ROLES: dict[str, str] = {
    "": "—",
    "instagram": "אינסטגרם",
    "tiktok": "טיקטוק",
    "facebook": "פייסבוק",
    "youtube": "יוטיוב",
    "linkedin": "לינקדאין",
    "website": "אתר / דף נחיתה",
}

CHOICE_KINDS = ("choice", "multi")
_KEY = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def new_key(prefix: str = "q") -> str:
    """A fresh, stable identifier for a question or section."""
    return f"{prefix}_{secrets.token_hex(3)}"


# --------------------------------------------------------------------- default


def _q(key: str, label: str, kind: str = "text", required: bool = False,
       hint: str = "", role: str = "", options: Optional[list[str]] = None) -> dict[str, Any]:
    return {"key": key, "label": label, "kind": kind, "required": required,
            "hint": hint, "role": role, "options": options or [], "scale_max": 10}


#: The questionnaire the system starts with (the one clients got until the
#: editor existed). Seeded into the store once; after that the store is the truth.
DEFAULT_DEFINITION: dict[str, Any] = {
    "id": "strategy",
    "title": "שאלון הכנה לבניית אסטרטגיה",
    "intro": "כמה שאלות שיעזרו לנו לבנות לך אסטרטגיה מדויקת. לוקח בערך 10 דקות.",
    "thanks": "קיבלנו את התשובות. דרור ייגש לבניית האסטרטגיה עבורך.",
    "version": 1,
    "sections": [
        {"id": "business", "title": "פרטי העסק", "description": "", "questions": [
            _q("business_name", "שם העסק / המותג", required=True),
            _q("field", "תחום העיסוק", required=True,
               hint="למשל: קורס דיגיטל, הכשרה מקצועית, ייעוץ"),
            _q("offering", "מה בדיוק אתם מוכרים?", "textarea", required=True),
            _q("price_point", "טווח מחירים של המוצר/השירות המרכזי"),
        ]},
        {"id": "audience", "title": "קהל היעד", "description": "", "questions": [
            _q("ideal_client", "מיהו הלקוח האידיאלי שלכם?", "textarea", required=True),
            _q("pain_points", "מהם הכאבים / הצרכים המרכזיים של הקהל?", "textarea"),
            _q("objections", "מהן ההתנגדויות הנפוצות שאתם שומעים לפני רכישה?", "textarea"),
        ]},
        {"id": "market", "title": "שוק ומתחרים", "description": "", "questions": [
            _q("competitors", "מי המתחרים המרכזיים שלכם?", "textarea"),
            _q("differentiation", "מה מבדל אתכם מהמתחרים?", "textarea", required=True),
        ]},
        {"id": "digital", "title": "נוכחות דיגיטלית",
         "description": "הקישורים עוזרים לנו לנתח את התוכן הקיים שלכם.", "questions": [
            _q("instagram", "קישור לאינסטגרם", "url", role="instagram"),
            _q("tiktok", "קישור לטיקטוק", "url", role="tiktok"),
            _q("facebook", "קישור לפייסבוק", "url", role="facebook"),
            _q("youtube", "קישור ליוטיוב", "url", role="youtube"),
            _q("website", "אתר / דף נחיתה", "url", role="website"),
        ]},
        {"id": "goals", "title": "שיווק נוכחי ומטרות", "description": "", "questions": [
            _q("current_marketing", "מה אתם עושים היום מבחינה שיווקית?", "textarea"),
            _q("whats_working", "מה עובד לכם היום, ומה פחות?", "textarea"),
            _q("goals", "מה היעד העסקי לחצי השנה הקרובה?", "textarea", required=True),
        ]},
    ],
}


def default_definition() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_DEFINITION)


def blank_definition(qid: str, title: str) -> dict[str, Any]:
    return {"id": qid, "title": title or "שאלון חדש", "intro": "", "thanks": "תודה! קיבלנו את התשובות.",
            "version": 0, "sections": [{"id": new_key("s"), "title": "חלק ראשון", "description": "",
                                        "questions": [_q(new_key(), "שאלה ראשונה")]}]}


# ------------------------------------------------------------------ structure


def normalize(defn: dict[str, Any]) -> dict[str, Any]:
    """Coerce an edited definition into the canonical shape, filling defaults.

    Keys the editor did not send are generated, never guessed from the label.
    """
    out: dict[str, Any] = {
        "id": str(defn.get("id") or "").strip(),
        "title": str(defn.get("title") or "").strip(),
        "intro": str(defn.get("intro") or "").strip(),
        "thanks": str(defn.get("thanks") or "").strip(),
        "version": int(defn.get("version") or 0),
        "sections": [],
    }
    for s in defn.get("sections") or []:
        section = {"id": str(s.get("id") or new_key("s")), "title": str(s.get("title") or "").strip(),
                   "description": str(s.get("description") or "").strip(), "questions": []}
        for q in s.get("questions") or []:
            kind = str(q.get("kind") or "text")
            options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
            try:
                scale_max = int(q.get("scale_max") or 10)
            except (TypeError, ValueError):
                scale_max = 10
            section["questions"].append({
                "key": str(q.get("key") or new_key()).strip(),
                "label": str(q.get("label") or "").strip(),
                "kind": kind,
                "required": bool(q.get("required")),
                "hint": str(q.get("hint") or "").strip(),
                "role": str(q.get("role") or "") if kind == "url" else "",
                "options": options if kind in CHOICE_KINDS else [],
                "scale_max": scale_max if kind == "scale" else 10,
            })
        out["sections"].append(section)
    return out


def validate(defn: dict[str, Any]) -> list[str]:
    """Problems that would make the form unusable, in Hebrew, for the editor."""
    errors: list[str] = []
    if not defn.get("title"):
        errors.append("לשאלון חסרה כותרת.")
    sections = defn.get("sections") or []
    if not sections:
        errors.append("צריך לפחות חלק אחד.")
    keys: set[str] = set()
    roles: dict[str, str] = {}
    total = 0
    for i, s in enumerate(sections, 1):
        name = s.get("title") or f"חלק {i}"
        if not s.get("title"):
            errors.append(f"לחלק {i} חסרה כותרת.")
        if not s.get("questions"):
            errors.append(f"בחלק \"{name}\" אין שאלות.")
        for j, q in enumerate(s.get("questions") or [], 1):
            total += 1
            where = f"בחלק \"{name}\", שאלה {j}"
            if not q.get("label"):
                errors.append(f"{where}: חסר נוסח לשאלה.")
            if q.get("kind") not in KINDS:
                errors.append(f"{where}: סוג שאלה לא מוכר ({q.get('kind')}).")
            key = q.get("key", "")
            if not _KEY.match(key):
                errors.append(f"{where}: מזהה פנימי לא תקין ({key}).")
            elif key in keys:
                errors.append(f"{where}: המזהה {key} כבר בשימוש בשאלה אחרת.")
            keys.add(key)
            if q.get("kind") in CHOICE_KINDS and len(q.get("options") or []) < 2:
                errors.append(f"{where}: בשאלת בחירה צריך לפחות שתי אפשרויות.")
            if q.get("kind") == "scale" and not 2 <= int(q.get("scale_max") or 0) <= 10:
                errors.append(f"{where}: סולם הדירוג צריך להיות בין 2 ל-10.")
            role = q.get("role") or ""
            if role:
                if role not in ROLES:
                    errors.append(f"{where}: שימוש לא מוכר ({role}).")
                elif role in roles:
                    errors.append(f"{where}: כבר יש שאלה שמסומנת כ\"{ROLES[role]}\" ({roles[role]}).")
                roles[role] = q.get("label") or key
    if sections and total == 0:
        errors.append("בשאלון אין אף שאלה.")
    return errors


def questions(defn: dict[str, Any]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """``(section, question)`` in form order."""
    for s in defn.get("sections") or []:
        for q in s.get("questions") or []:
            yield s, q


def snapshot(defn: dict[str, Any]) -> list[dict[str, Any]]:
    """What a response stores about the questions it answered."""
    return [{"key": q["key"], "label": q["label"], "section": s["title"],
             "kind": q["kind"], "role": q.get("role") or ""} for s, q in questions(defn)]


# -------------------------------------------------------------------- answers


def display(value: Any) -> str:
    """An answer as text (multi-choice answers are lists)."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if str(v).strip())
    return "" if value is None else str(value).strip()


def _normalize_url(value: str) -> str:
    value = value.strip()
    if value and not re.match(r"^https?://", value, re.I) and "." in value and " " not in value:
        return "https://" + value
    return value


def parse_answers(defn: dict[str, Any], form: dict[str, list[str]]) -> dict[str, Any]:
    """Answers from a submitted form (``parse_qs`` shape), keyed by question key."""
    out: dict[str, Any] = {}
    for _s, q in questions(defn):
        values = [v.strip() for v in form.get(q["key"], []) if v.strip()]
        if q["kind"] == "multi":
            out[q["key"]] = [v for v in values if v in q["options"]]
        else:
            value = values[0] if values else ""
            out[q["key"]] = _normalize_url(value) if q["kind"] == "url" else value
    return out


def problems(defn: dict[str, Any], answers: dict[str, Any]) -> dict[str, str]:
    """``{question key: Hebrew message}`` for everything the client must fix."""
    out: dict[str, str] = {}
    for _s, q in questions(defn):
        value = answers.get(q["key"])
        text = display(value)
        if not text:
            if q.get("required"):
                out[q["key"]] = "שדה חובה"
            continue
        kind = q["kind"]
        if kind == "email" and not _EMAIL.match(text):
            out[q["key"]] = "כתובת אימייל לא תקינה"
        elif kind == "url" and not re.match(r"^https?://[^\s]+\.[^\s]+", text, re.I):
            out[q["key"]] = "קישור לא תקין"
        elif kind == "number" and not re.fullmatch(r"-?\d+([.,]\d+)?", text.replace(",", "")):
            out[q["key"]] = "צריך להזין מספר"
        elif kind == "choice" and text not in q["options"]:
            out[q["key"]] = "צריך לבחור אחת מהאפשרויות"
        elif kind == "scale":
            if not text.isdigit() or not 1 <= int(text) <= int(q.get("scale_max") or 10):
                out[q["key"]] = "צריך לבחור ערך בסולם"
        elif kind == "tel" and len(re.sub(r"\D", "", text)) < 9:
            out[q["key"]] = "מספר טלפון לא תקין"
    return out


def social_profiles(snap: list[dict[str, Any]], answers: dict[str, Any]) -> dict[str, str]:
    """``{role: url}`` — the links the AI analysis should open, by role."""
    out: dict[str, str] = {}
    for q in snap:
        role = q.get("role") or ""
        url = display(answers.get(q["key"]))
        if role and url:
            out[role] = url
    return out


def as_text(snap: list[dict[str, Any]], answers: dict[str, Any]) -> str:
    """The answers as plain text for a prompt, grouped by section, blanks omitted."""
    lines: list[str] = []
    current = None
    for q in snap:
        value = display(answers.get(q["key"]))
        if not value:
            continue
        if q.get("section") != current:
            current = q.get("section")
            lines.append(f"\n## {current}")
        lines.append(f"- {q['label']}: {value}")
    return "\n".join(lines).strip()


def to_document_html(title: str, client_name: str, snap: list[dict[str, Any]],
                     answers: dict[str, Any]) -> str:
    """The body of the Google Doc saved to the client's folder.

    Unanswered questions are omitted — an empty ``—`` under every skipped
    optional field makes the doc look unfinished rather than concise.
    """
    def esc(v: Any) -> str:
        return html.escape(str(v or ""))

    parts = [f"<h1>{esc(title)}</h1>", f"<p><strong>לקוח:</strong> {esc(client_name)}</p>", "<hr>"]
    current = None
    for q in snap:
        value = display(answers.get(q["key"]))
        if not value:
            continue
        if q.get("section") != current:
            current = q.get("section")
            parts.append(f"<h2>{esc(current)}</h2>")
        body = esc(value).replace("\n", "<br>")
        parts.append(f"<p><strong>{esc(q['label'])}</strong><br>{body}</p>")
    return f'<div dir="rtl" lang="he">{"".join(parts)}</div>'

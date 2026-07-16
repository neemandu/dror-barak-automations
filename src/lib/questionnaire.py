"""The strategy-prep questionnaire — שאלון הכנה לבניית אסטרטגיה.

Our own form, not Google Forms. Hosting it ourselves means the client is known
from the signed link (no guessing which response belongs to whom), we act the
instant it is submitted, and the answers go straight into a Google Doc in the
client's Drive folder.

The questions live here, editable, the same way the contract and the email copy
do — Dror changes the text and it takes effect, no Google Forms UI, no code.
Reuses the social-profile answers to drive the last-5-videos analysis, so ask for
those links plainly.

Each question: a ``key`` (stored, used in the doc), a Hebrew ``label``, a
``kind`` (text / textarea / url / email), and whether it's ``required``.
"""

from __future__ import annotations

import html
from typing import Any, NamedTuple


class Question(NamedTuple):
    key: str
    label: str
    kind: str = "text"       # text | textarea | url | email | tel
    required: bool = False
    hint: str = ""


class Section(NamedTuple):
    title: str
    questions: list[Question]


# Editable. Add, remove or reword freely — the doc and the form both read this.
SECTIONS: list[Section] = [
    Section("פרטי העסק", [
        Question("business_name", "שם העסק / המותג", required=True),
        Question("field", "תחום העיסוק", required=True,
                 hint="למשל: קורס דיגיטל, הכשרה מקצועית, ייעוץ"),
        Question("offering", "מה בדיוק אתם מוכרים?", "textarea", required=True),
        Question("price_point", "טווח מחירים של המוצר/השירות המרכזי"),
    ]),
    Section("קהל היעד", [
        Question("ideal_client", "מיהו הלקוח האידיאלי שלכם?", "textarea", required=True),
        Question("pain_points", "מהם הכאבים / הצרכים המרכזיים של הקהל?", "textarea"),
        Question("objections", "מהן ההתנגדויות הנפוצות שאתם שומעים לפני רכישה?", "textarea"),
    ]),
    Section("שוק ומתחרים", [
        Question("competitors", "מי המתחרים המרכזיים שלכם?", "textarea"),
        Question("differentiation", "מה מבדל אתכם מהמתחרים?", "textarea", required=True),
    ]),
    Section("נוכחות דיגיטלית", [
        # These feed the last-5-videos analysis — ask for real profile links.
        Question("instagram", "קישור לאינסטגרם", "url"),
        Question("tiktok", "קישור לטיקטוק", "url"),
        Question("facebook", "קישור לפייסבוק", "url"),
        Question("youtube", "קישור ליוטיוב", "url"),
        Question("website", "אתר / דף נחיתה", "url"),
    ]),
    Section("שיווק נוכחי ומטרות", [
        Question("current_marketing", "מה אתם עושים היום מבחינה שיווקית?", "textarea"),
        Question("whats_working", "מה עובד לכם היום, ומה פחות?", "textarea"),
        Question("goals", "מה היעד העסקי לחצי השנה הקרובה?", "textarea", required=True),
    ]),
]


def all_questions() -> list[Question]:
    return [q for s in SECTIONS for q in s.questions]


def required_keys() -> list[str]:
    return [q.key for q in all_questions() if q.required]


# Profile fields whose answers the social-media analysis should read.
SOCIAL_KEYS = ("instagram", "tiktok", "facebook", "youtube")


def social_profiles(answers: dict[str, str]) -> dict[str, str]:
    """The profile links the client gave, for the last-5-videos analysis."""
    return {k: v.strip() for k in SOCIAL_KEYS if (v := answers.get(k, "").strip())}


def missing(answers: dict[str, str]) -> list[str]:
    """Required questions left blank, by their Hebrew label."""
    by_key = {q.key: q for q in all_questions()}
    return [by_key[k].label for k in required_keys()
            if not str(answers.get(k) or "").strip()]


def to_document_html(client_name: str, answers: dict[str, str]) -> str:
    """Format the answers as the body of the Google Doc saved to Drive.

    Questions with no answer are omitted — an empty ``—`` under every skipped
    optional field makes the doc look unfinished rather than concise.
    """
    def esc(v: Any) -> str:
        return html.escape(str(v or ""))

    parts = [
        f'<h1>שאלון הכנה לבניית אסטרטגיה</h1>',
        f'<p><strong>לקוח:</strong> {esc(client_name)}</p>',
        "<hr>",
    ]
    for section in SECTIONS:
        answered = [(q, answers.get(q.key, "").strip()) for q in section.questions]
        answered = [(q, a) for q, a in answered if a]
        if not answered:
            continue
        parts.append(f"<h2>{esc(section.title)}</h2>")
        for q, a in answered:
            parts.append(f"<p><strong>{esc(q.label)}</strong><br>{esc(a)}</p>")
    return f'<div dir="rtl" lang="he">{"".join(parts)}</div>'

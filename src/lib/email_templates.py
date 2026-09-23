"""The emails Dror sends, wording and look in one editable place.

Dror asked for his message copy to live somewhere he can change it. WhatsApp
can't honour that any more: on the official Meta API every outbound message is a
Meta-approved template, and rewording means resubmitting for approval. **Email
has no such constraint**, so this is where that promise is actually kept: edit
the text below and it goes out changed, no approval, no deploy.

Each template is a ``str.format`` string with ``{placeholders}``. Bodies are
written as plain text and wrapped by :func:`layout` in the branded card (the
brand band on top, Dror's signature at the bottom), so the copy stays readable
here rather than buried in markup.

They go out in Dror's name, so they are written the way a person writes: no long
dashes, no "תמלא/י" slashes, no emoji in a client's subject line
(``tests/test_house_style.py`` holds the line).
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, NamedTuple

from . import config


class EmailTemplate(NamedTuple):
    subject: str
    body: str  # plain text; blank lines are paragraphs
    cta: str = ""  # optional button label; needs a {cta_url} param
    internal: bool = False  # to Dror himself: no signature, a system footer instead


TEMPLATES: dict[str, EmailTemplate] = {
    # Sent when Dror presses `שלח הצעת מחיר`. The contract itself is at the link;
    # the mail's job is to get it opened. The body is Dror's own wording.
    "sign_contract": EmailTemplate(
        subject="ההסכם והמפרט לחתימה",
        body=(
            "היי {client_name},\n"
            "\n"
            "בהמשך לפגישה שלנו, אני מקווה שיצא לך לעבור על ההסכם והמפרט לצמיחה "
            "ברווחים שלך.\n"
            "במידה ויש שאלות, אני כאן בשבילך.\n"
            "לתחילת עבודה יש לחתום דיגיטלית ואנחנו יוצאים לדרך :)"
        ),
        cta="לחתימה על ההסכם",
    ),
    # Sent after signing: the strategy questionnaire. Its answers become the
    # Google Doc that seeds the whole strategy, so it matters that they fill it.
    "questionnaire": EmailTemplate(
        subject="שאלון קצר לפני שמתחילים",
        body=(
            "היי {client_name},\n"
            "\n"
            "שמח שאנחנו מתחילים לעבוד יחד.\n"
            "כדי לבנות לך אסטרטגיה מדויקת אני צריך להכיר את העסק קצת יותר לעומק, "
            "אז הכנתי שאלון קצר. זה לוקח בערך עשר דקות, והתשובות שלך הן הבסיס לכל "
            "מה שנבנה."
        ),
        cta="למילוי השאלון",
    ),
    # Chases an unfilled questionnaire. Without the answers the strategy and the
    # social prep have nothing to work from, so the client is stuck at the start
    # of the engagement they just paid for. Worth two nudges.
    "questionnaire_reminder": EmailTemplate(
        subject="תזכורת קטנה לגבי השאלון",
        body=(
            "היי {client_name},\n"
            "\n"
            "רציתי להזכיר שהשאלון עדיין מחכה לך. בלי התשובות שלך אני לא יכול להתחיל "
            "לבנות את האסטרטגיה, וזה לוקח רק כמה דקות.\n"
            "\n"
            "אם משהו לא ברור, אפשר פשוט להשיב למייל הזה."
        ),
        cta="למילוי השאלון",
    ),
    # Chases an unsigned contract. Same link as the original: a client who lost
    # the first email can sign from this one. Gentle, a nudge rather than a demand.
    "sign_reminder": EmailTemplate(
        subject="ההסכם עדיין מחכה לחתימה",
        body=(
            "היי {client_name},\n"
            "\n"
            "רק מזכיר שההסכם והמפרט לצמיחה ברווחים שלך עדיין מחכים לחתימה. "
            "החתימה דיגיטלית ולוקחת רגע, ומיד אחריה אנחנו יוצאים לדרך.\n"
            "\n"
            "יש שאלות? אני כאן."
        ),
        cta="לחתימה על ההסכם",
    ),
    # Sent to Dror the moment a client signs: the most important event in the
    # funnel, which he otherwise learns only from a task comment or the next day's
    # digest. The signed PDF is attached.
    "signed_notification": EmailTemplate(
        subject="ההסכם עם {client_name} נחתם",
        body=(
            "היי דרור,\n"
            "\n"
            "ההסכם עם {client_name} נחתם עכשיו. העותק החתום מצורף, ונשמר גם בתיקיית "
            "הלקוח בדרייב.\n"
            "\n"
            "תאריך החתימה: {signed_at}\n"
            "טביעת אצבע של המסמך: {fingerprint}\n"
            "\n"
            "האונבורדינג יוצא לדרך אוטומטית."
        ),
        internal=True,
    ),
    # Sent to Dror when the strategy bot (T8) has saved a draft to Drive. Email
    # rather than WhatsApp: on the official Meta API Dror's own notifications
    # would each need an approved template and be billed.
    "strategy_ready": EmailTemplate(
        subject="טיוטת אסטרטגיה מוכנה לבדיקה: {client_name}",
        body=(
            "היי דרור,\n"
            "\n"
            "טיוטת האסטרטגיה של {client_name} מוכנה, ונשמרה כמסמך עריך בתיקיית הלקוח "
            "בדרייב.\n"
            "כדאי לעבור עליה לפני שהיא יוצאת ללקוח."
        ),
        cta="לפתיחת האסטרטגיה",
        internal=True,
    ),
    # Sent to Dror when a monthly campaign report is ready (T7). Addressed to him,
    # not the client: he reviews it and forwards it on. The PDF is attached and
    # the button opens it in Drive.
    "campaign_report_ready": EmailTemplate(
        subject="דוח קמפיינים לאישור: {client_name}, {month_label}",
        body=(
            "היי דרור,\n"
            "\n"
            "דוח הקמפיינים של {client_name} לחודש {month_label} מוכן ומחכה לאישור שלך. "
            "הוא מצורף כ-PDF ונשמר גם בתיקיית הלקוח בדרייב.\n"
            "\n"
            "בקצרה:\n"
            "הוצאה: {spend}\n"
            "לידים: {leads}\n"
            "עלות לליד: {cost_per_lead}\n"
            "\n"
            "אם הכול נראה טוב, אפשר להעביר אותו ללקוח."
        ),
        cta="לפתיחת הדוח",
        internal=True,
    ),
}

#: Dror's signature under every client email. Phone comes from PROVIDER_PHONE.
SIGNATURE_NAME = "דרור ברק"
SIGNATURE_TITLE = "ליווי עסקים והגדלת מכירות"
SIGNATURE_SITE = "drorbrk.co.il"

#: The brand band on top of the card, sent inline (``cid:``) rather than linked:
#: Gmail drops ``data:`` images, and a hosted one would hang on our API being up.
BAND_CID = "brand-band"
BAND_PATH = Path(__file__).resolve().parents[2] / "templates" / "assets" / "email_band.png"

_FONT = "Arial,'Segoe UI',Helvetica,sans-serif"
_INK, _MUTED, _LINE, _GROUND, _BRAND = "#14171a", "#5b6472", "#e3e8ef", "#f2f5f8", "#1f6fd6"


class TemplateError(KeyError):
    """Raised when a template name is unknown or a parameter is missing."""


def _paragraphs(text: str) -> str:
    """Plain-text copy into RTL paragraphs, escaping as we go."""
    out = []
    for block in text.split("\n\n"):
        lines = [html.escape(line) for line in block.split("\n") if line.strip()]
        if lines:
            out.append(f'<p style="margin:0 0 16px">{"<br>".join(lines)}</p>')
    return "".join(out)


def _phone() -> str:
    digits = re.sub(r"\D", "", config.get("PROVIDER_PHONE", ""))
    return f"{digits[:3]}-{digits[3:]}" if len(digits) == 10 else digits


def _signature_html() -> str:
    phone = _phone()
    contact = [f'<a href="https://{SIGNATURE_SITE}" style="color:{_BRAND};text-decoration:none">'
               f"{SIGNATURE_SITE}</a>"]
    if phone:
        contact.insert(0, f'<a href="tel:{phone.replace("-", "")}" '
                          f'style="color:{_BRAND};text-decoration:none">{phone}</a>')
    return (
        f'<tr><td dir="rtl" style="padding:6px 36px 32px;font-family:{_FONT};text-align:right">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td style="border-top:1px solid {_LINE};padding-top:18px;font-family:{_FONT};'
        f'font-size:14px;line-height:1.7;color:{_MUTED};text-align:right">'
        f'<div style="font-size:16px;font-weight:bold;color:{_INK}">{SIGNATURE_NAME}</div>'
        f"<div>{SIGNATURE_TITLE}</div>"
        f'<div>{" &nbsp;|&nbsp; ".join(contact)}</div>'
        f"</td></tr></table></td></tr>"
    )


def _signature_text() -> str:
    return "\n\n" + "\n".join(x for x in (SIGNATURE_NAME, SIGNATURE_TITLE, _phone(), SIGNATURE_SITE) if x)


def button(label: str, url: str) -> str:
    """A button that survives Outlook, with the link spelled out underneath:
    not everyone clicks buttons, and some clients strip them."""
    href = html.escape(url, quote=True)
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:10px 0 6px">'
        f'<tr><td bgcolor="{_BRAND}" style="border-radius:10px;background:{_BRAND}">'
        f'<a href="{href}" style="display:inline-block;padding:14px 34px;font-family:{_FONT};'
        f'font-size:16px;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:10px">'
        f"{html.escape(label)}</a></td></tr></table>"
        f'<p style="margin:14px 0 8px;font-size:13px;line-height:1.6;color:{_MUTED}">'
        f"אם הכפתור לא נפתח, אפשר להיכנס מכאן:<br>"
        f'<a href="{href}" style="color:{_BRAND};word-break:break-all">{html.escape(url)}</a></p>'
    )


def layout(body_html: str, *, internal: bool = False, preheader: str = "", width: int = 600) -> str:
    """The branded card every email is sent in.

    Tables and inline styles only: that is what Gmail, Outlook and Apple Mail
    agree on. A client email closes with Dror's signature; one to Dror himself
    says it came from the automations instead.
    """
    footer = (
        f'<p style="margin:14px 0 0;font-family:{_FONT};font-size:12px;color:#8a93a0;text-align:center">'
        f"נשלח ממערכת האוטומציות</p>" if internal else ""
    )
    return (
        '<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light"></head>'
        f'<body style="margin:0;padding:0;background:{_GROUND}">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(preheader)}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{_GROUND}"><tr><td align="center" style="padding:28px 12px">'
        f'<table role="presentation" width="{width}" cellpadding="0" cellspacing="0" border="0" dir="rtl" '
        f'style="width:100%;max-width:{width}px;background:#ffffff;border:1px solid {_LINE};border-radius:14px">'
        f'<tr><td style="padding:0;line-height:0;font-size:0">'
        f'<img src="cid:{BAND_CID}" width="{width}" alt="דרור ברק" '
        f'style="display:block;width:100%;max-width:{width}px;height:auto;border:0;border-radius:14px 14px 0 0"></td></tr>'
        f'<tr><td dir="rtl" style="padding:30px 36px 14px;font-family:{_FONT};font-size:16px;'
        f'line-height:1.75;color:{_INK};text-align:right">{body_html}</td></tr>'
        f'{"" if internal else _signature_html()}'
        f"</table>{footer}</td></tr></table></body></html>"
    )


def inline_images() -> dict[str, bytes]:
    """The ``cid:`` images :func:`layout` refers to, for the sender to attach."""
    return {BAND_CID: BAND_PATH.read_bytes()}


def render(name: str, **params: Any) -> dict[str, Any]:
    """Return ``{"subject", "html", "text", "inline"}`` for a template.

    Both parts are produced: some clients refuse HTML, and a contract link that
    only exists in the HTML half is a link some client will never see.
    """
    try:
        template = TEMPLATES[name]
    except KeyError:
        raise TemplateError(
            f"Unknown email template '{name}'. Known: {', '.join(sorted(TEMPLATES))}."
        ) from None

    try:
        subject = template.subject.format(**params)
        text = template.body.format(**params)
    except KeyError as exc:
        raise TemplateError(f"Template '{name}' is missing parameter {exc}.") from None

    body_html = _paragraphs(text)
    if template.cta:
        url = params.get("cta_url")
        if not url:
            raise TemplateError(
                f"Template '{name}' has a button but no cta_url: the mail would "
                f"ask the client to sign and give them nothing to click."
            )
        body_html += button(template.cta, str(url))
        text += f"\n\n{template.cta}:\n{url}"
    if not template.internal:
        text += _signature_text()

    preheader = text.split("\n\n", 1)[-1].split("\n", 1)[0][:120]
    return {
        "subject": subject,
        "html": layout(body_html, internal=template.internal, preheader=preheader),
        "text": text,
        "inline": inline_images(),
    }

"""The emails Dror sends clients — wording in one editable place.

Dror asked for his message copy to live somewhere he can change it. WhatsApp
can't honour that any more: on the official Meta API every outbound message is a
Meta-approved template, and rewording means resubmitting for approval. **Email
has no such constraint**, so this is where that promise is actually kept — edit
the text below and it goes out changed, no approval, no deploy.

Each template is a ``str.format`` string with ``{placeholders}``. Bodies are
written as plain text and wrapped in RTL HTML by :func:`render`, so the copy stays
readable here rather than buried in markup.

The signature is Dror's own, exactly as he writes it.
"""

from __future__ import annotations

import html
from typing import Any, NamedTuple


class EmailTemplate(NamedTuple):
    subject: str
    body: str  # plain text; blank lines are paragraphs
    cta: str = ""  # optional button label; needs a {cta_url} param


TEMPLATES: dict[str, EmailTemplate] = {
    # Sent when Dror presses `שלח הצעת מחיר`. The contract itself is at the link;
    # the mail's job is to get it opened.
    "sign_contract": EmailTemplate(
        subject="ההסכם והמפרט לחתימה — דרור ברק",
        body=(
            'היי {client_name}\n'
            "בהמשך לפגישה שלנו, אני מקווה שיצא לך לעבור על הסכם והמפרט לצמיחה "
            "ברווחים שלך.\n"
            "במידה ויש שאלות, אני כאן בשבילך.\n"
            "לתחילת עבודה יש לחתום דיגיטלית ואנחנו יוצאים לדרך :)\n"
            "\n"
            "דרור ברק"
        ),
        cta="לחתימה על ההסכם",
    ),
    # Sent on the 1st, once the חשבון עסקה exists. "מצ״ב" is a promise: the PDF is
    # attached, so this template must never be sent without it.
    "monthly_proforma": EmailTemplate(
        subject="חשבון עסקה לחודש {month} — דרור ברק",
        body=(
            'היי {client_name}\n'
            "\n"
            'מצ"ב חשבון עסקה להסדרת התשלום על החודש הקרוב,\n'
            "אודה לטיפולך :)\n"
            "\n"
            "שבוע מעולה,\n"
            "דרור ברק :)"
        ),
    ),
}


class TemplateError(KeyError):
    """Raised when a template name is unknown or a parameter is missing."""


_CSS = (
    "font-family:system-ui,'Segoe UI',Arial,sans-serif;font-size:15px;"
    "line-height:1.9;color:#1a1d21"
)


def _to_html(text: str) -> str:
    """Plain-text copy into RTL HTML paragraphs, escaping as we go."""
    blocks = [b for b in text.split("\n\n")]
    out = []
    for block in blocks:
        lines = [html.escape(line) for line in block.split("\n") if line.strip() != ""]
        if lines:
            out.append("<p>" + "<br>".join(lines) + "</p>")
    return "".join(out)


def render(name: str, **params: Any) -> dict[str, str]:
    """Return ``{"subject", "html", "text"}`` for a template.

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

    body_html = _to_html(text)

    if template.cta:
        url = params.get("cta_url")
        if not url:
            raise TemplateError(
                f"Template '{name}' has a button but no cta_url — the mail would "
                f"ask the client to sign and give them nothing to click."
            )
        body_html += (
            f'<p style="margin:28px 0"><a href="{html.escape(str(url))}" '
            f'style="background:#00a8f0;color:#fff;text-decoration:none;'
            f'padding:14px 30px;border-radius:8px;display:inline-block;'
            f'font-weight:600">{html.escape(template.cta)}</a></p>'
            # Not everyone clicks buttons, and some clients strip them.
            f'<p style="font-size:13px;color:#5b6472">או העתק/י את הקישור:<br>'
            f'<a href="{html.escape(str(url))}">{html.escape(str(url))}</a></p>'
        )
        text += f"\n\n{template.cta}:\n{url}"

    return {
        "subject": subject,
        "html": f'<div dir="rtl" style="{_CSS}">{body_html}</div>',
        "text": text,
    }

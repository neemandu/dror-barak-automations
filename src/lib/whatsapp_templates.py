"""Editable WhatsApp message templates.

Dror asked for one tidy place holding the bodies of the WhatsApp messages (with
parameters) so he can change the wording whenever he likes without touching code.

Each template is a Python ``str.format`` string. Call :func:`render` with the
template name and the parameters it needs. Placeholder Hebrew copy below is a
starting point — Dror should review and adjust the wording (see Open Question #8).
"""

from __future__ import annotations

from typing import Any

TEMPLATES: dict[str, str] = {
    # Sent after the initial meeting, asking the lead to fill the questionnaire.
    "questionnaire": (
        "היי {first_name}, תודה על הפגישה! 🙏\n"
        "כדי שנתכונן בצורה הטובה ביותר, נשמח שתמלא/י שאלון קצר:\n"
        "{questionnaire_url}"
    ),
    # Sent with the monthly payment request / invoice link.
    "payment_request": (
        "היי {first_name}, הופקה דרישת תשלום עבור חודש {month}.\n"
        "לתשלום מאובטח: {payment_url}\n"
        "תודה, דרור ברק."
    ),
    # Sent when a new client's WhatsApp channel is opened during onboarding.
    "onboarding_welcome": (
        "ברוך/ה הבא/ה {first_name}! 🎉\n"
        "שמחים להתחיל לעבוד יחד. פתחנו ערוץ הזה לכל התיאומים והעדכונים."
    ),
    # Daily end-of-day summary that Dror himself receives.
    "daily_summary": (
        "סיכום יומי — {date} 📊\n"
        "{body}"
    ),
    # Internal ping to Dror to choose which templates to copy for a new client.
    "onboarding_dror_prompt": (
        "לקוח חדש נחתם: {client_name}.\n"
        "נפתחה תיקייה בדרייב: {drive_url}\n"
        "אילו טמפלטים להעתיק לתיקייה? השב/י כאן."
    ),
}


class TemplateError(KeyError):
    """Raised when a template name is unknown or a parameter is missing."""


def render(name: str, **params: Any) -> str:
    """Render a named template with the given parameters.

    Raises :class:`TemplateError` with a clear message if the template does not
    exist or a required ``{placeholder}`` was not supplied.
    """
    try:
        template = TEMPLATES[name]
    except KeyError:
        raise TemplateError(
            f"Unknown WhatsApp template '{name}'. "
            f"Known: {', '.join(sorted(TEMPLATES))}."
        ) from None
    try:
        return template.format(**params)
    except KeyError as exc:
        raise TemplateError(
            f"Template '{name}' is missing parameter {exc}."
        ) from None

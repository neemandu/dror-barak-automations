"""Buttons — the actions Dror runs by hand from a ClickUp task.

Some work isn't a lifecycle event. Dror decides *when* to send a quote, when a
strategy is worth generating, when to rebuild a campaign report. That needs a
button, not a status change.

ClickUp's **Button Custom Field** does exactly this: clicking it runs an
Automation, and the Automation's "Call webhook" action POSTs to our Lambda. A
button holds no value, so unlike a checkbox or a tag there is nothing to clear
afterwards — and therefore no risk of our own cleanup firing the webhook again.

Wiring, per button (see docs/CLICKUP_SETUP.md):

    Button field "שלח הצעת מחיר"
      -> Automation: when button clicked -> Call webhook
         URL:    https://<api>/action?action=send_quote
         Header: X-Automation-Token: <AUTOMATION_TOKEN>

The action is named in the URL because Automation webhooks send a static payload
that does not say which button was pressed — only which task it was pressed on.

Automation webhooks are **not** signed the way API webhooks are, which is why the
token header is not optional: without it the endpoint is an open invitation to
send quotes to Dror's clients.
"""

from __future__ import annotations

from typing import Any, Callable, NamedTuple, Optional


class Action(NamedTuple):
    key: str  # the ?action= value
    label: str  # Hebrew — the button text Dror sees
    confirm: str  # commented back on the task when it succeeds
    # False when re-running is legitimate (a revised quote, a rebuilt report).
    # True would mean "never twice for this client, whatever is clicked".
    once_only: bool

    def run(self, client_id: str, dry_run: bool) -> dict[str, Any]:
        return _RUNNERS[self.key](client_id, dry_run)


ACTIONS: dict[str, Action] = {
    "send_quote": Action(
        key="send_quote",
        label="שלח הצעת מחיר",
        confirm="✅ נשלחה הצעת מחיר ללקוח",
        # A revised quote is normal. Blocking the second send would be wrong.
        once_only=False,
    ),
    "send_questionnaire": Action(
        key="send_questionnaire",
        label="שלח שאלון",
        confirm="✅ נשלח שאלון ללקוח",
        once_only=False,  # clients lose links; resending is routine
    ),
    "social_prep": Action(
        key="social_prep",
        label="בנה דוח רשתות",
        confirm="✅ דוח רשתות חברתיות מוכן",
        once_only=False,
    ),
    "strategy_bot": Action(
        key="strategy_bot",
        label="בנה אסטרטגיה",
        confirm="✅ אסטרטגיה נבנתה ונשמרה בדרייב",
        once_only=False,
    ),
    "campaign_summary": Action(
        key="campaign_summary",
        label="בנה דוח קמפיין",
        confirm="✅ דוח קמפיין מוכן וממתין לאישורך",
        once_only=False,
    ),
}

# Deliberately NOT a button:
#   onboarding — fires on `חתם`, and is guarded because it creates a Drive folder
#                nobody wants duplicated. A manual re-run risks a second one.


def _send_quote(client_id: str, dry_run: bool) -> dict[str, Any]:
    from ..automations import send_quote

    return send_quote.send(client_id, dry_run=dry_run)


def _send_questionnaire(client_id: str, dry_run: bool) -> dict[str, Any]:
    from ..automations import send_questionnaire

    return send_questionnaire.run(client_id, dry_run=dry_run)


def _social_prep(client_id: str, dry_run: bool) -> dict[str, Any]:
    from ..automations import social_prep

    return social_prep.run(client_id, dry_run=dry_run)


def _strategy_bot(client_id: str, dry_run: bool) -> dict[str, Any]:
    from ..automations import strategy_bot

    return strategy_bot.run(client_id, dry_run=dry_run)


def _campaign_summary(client_id: str, dry_run: bool) -> dict[str, Any]:
    from ..automations import campaign_summary

    return campaign_summary.run(client_id, dry_run=dry_run)


_RUNNERS: dict[str, Callable[[str, bool], dict[str, Any]]] = {
    "send_quote": _send_quote,
    "send_questionnaire": _send_questionnaire,
    "social_prep": _social_prep,
    "strategy_bot": _strategy_bot,
    "campaign_summary": _campaign_summary,
}


def get(key: str) -> Optional[Action]:
    return ACTIONS.get((key or "").strip())


def task_id_of(payload: dict[str, Any]) -> Optional[str]:
    """The task an Automation webhook fired on.

    ClickUp's current format nests the task under ``payload``; the legacy format
    puts it at the top level; and a hand-written custom body in the Automation UI
    tends to use ``task_id``. All three are accepted, because the alternative is a
    400 that looks like "the button is broken" for a body we could have understood.
    """
    inner = payload.get("payload")
    if isinstance(inner, dict):
        for key in ("id", "task_id"):
            if inner.get(key):
                return str(inner[key])
    for key in ("id", "task_id"):
        if payload.get(key):
            return str(payload[key])
    return None


def click_key(action_key: str, task_id: str, payload: dict[str, Any]) -> str:
    """Idempotency key for one button *press*.

    Keyed on the automation's own identifiers plus its timestamp: a retry of the
    same press repeats them, so it de-duplicates, while a second deliberate press
    has a new timestamp and is allowed through. That is the behaviour we want —
    Dror pressing "send quote" again after revising it must actually send.
    """
    auto_id = payload.get("auto_id") or ""
    trigger_id = payload.get("trigger_id") or ""
    date = payload.get("date") or ""
    return f"btn:{action_key}:{task_id}:{auto_id}:{trigger_id}:{date}"

"""The signing follow-up as a ClickUp task Dror can see and change.

When a contract goes out, a task opens on the משימות list:
``תזכורת חתימה: <client>``, due in 3 days, linked to the client, and its
description is the email the client will get. It is automatic: on the due date,
if the client has not signed, :mod:`src.automations.sign_reminders` sends the
description **as it reads then**, adds the signing button and Dror's signature,
comments ✅ and completes the task. Dror's control is the task itself:

* edit the description -> that is the text sent;
* move the due date -> it goes out then;
* close or delete the task -> it does not go out.

If the client signs first, the task closes itself with a note. A client whose
contract was sent before this existed (or a workspace without a משימות list)
gets the fixed template, as before.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from . import config, email_templates

PREFIX = "תזכורת חתימה: "
DAYS = 3
DAY_MS = 24 * 60 * 60 * 1000
STATUS_DONE = "complete"

_EXPLAIN = (
    "🤖 תזכורת אוטומטית. אם הלקוח לא יחתום עד תאריך היעד, המייל שבתיאור המשימה יישלח "
    "אליו אוטומטית באותו בוקר, עם כפתור החתימה והחתימה שלך.\n"
    "לערוך את הטקסט: משנים את התיאור. לשנות מתי: משנים את תאריך היעד. "
    "לבטל: סוגרים או מוחקים את המשימה."
)


def default_body(client: dict[str, Any]) -> str:
    """The follow-up text a new task starts with: Dror's template, with the name."""
    name = client.get("first_name") or client.get("name") or ""
    return email_templates.TEMPLATES["sign_reminder"].body.format(client_name=name)


def open_task(client_id: str, client: dict[str, Any], *, now: Optional[float] = None,
              dry_run: bool = False) -> Optional[str]:
    """Open the follow-up task for a contract just sent; returns its id.

    A re-sent contract with its task still open moves that task's due date
    instead of opening a second one; Dror's edits to the text are kept.
    ``None`` when there is no משימות list (the fixed template is used then).
    """
    from . import signing
    from .clients.clickup import ClickUpClient

    list_id = config.get("CLICKUP_TASKS_LIST_ID")
    if not list_id:
        return None
    clickup = ClickUpClient(dry_run=dry_run)
    due_ms = int(((now if now is not None else time.time()) * 1000) + DAYS * DAY_MS)

    previous = (signing.get_pending(client_id) or {}).get("reminder_task_id")
    if previous:
        task = fetch(clickup, str(previous))
        if task is not None and not is_closed(task):
            clickup.update_task(str(previous), due_date=due_ms)
            return str(previous)

    from . import review_tasks

    # The לקוח field, never simply the first Relationship field: that may be עובד.
    rel = review_tasks.client_field(clickup, str(list_id))
    made = clickup.create_task(
        str(list_id), f"{PREFIX}{client.get('name') or client_id}",
        description=default_body(client),
        custom_fields=[{"id": rel, "value": {"add": [client_id]}}] if rel else None,
        due_date=due_ms,
    )
    task_id = str(made.get("id") or "")
    if task_id:
        clickup.comment(task_id, _EXPLAIN)
    return task_id or None


def fetch(clickup: Any, task_id: str) -> Optional[dict[str, Any]]:
    """The task, or ``None`` if it was deleted (which cancels the follow-up)."""
    from .http import HttpError

    try:
        return clickup.get_task(task_id)
    except HttpError as exc:
        if exc.status in (401, 404):
            return None
        raise


def is_closed(task: dict[str, Any]) -> bool:
    return str((task.get("status") or {}).get("type") or "") == "closed"


def due_ms(task: dict[str, Any]) -> Optional[int]:
    value = task.get("due_date")
    return int(value) if value not in (None, "") else None


def body_of(task: dict[str, Any]) -> str:
    return str(task.get("text_content") or task.get("description") or "").strip()


def close(clickup: Any, task_id: str, note: str) -> None:
    clickup.comment(task_id, note)
    clickup.set_status(task_id, STATUS_DONE)


def close_on_signed(client_id: str) -> None:
    """The client signed: close their open follow-up, if any. Best-effort."""
    from . import signing
    from .clients.clickup import ClickUpClient

    task_id = (signing.get_pending(client_id) or {}).get("reminder_task_id")
    if not task_id:
        return
    try:
        clickup = ClickUpClient()
        task = fetch(clickup, str(task_id))
        if task is not None and not is_closed(task):
            close(clickup, str(task_id), "✅ הלקוח חתם, התזכורת לא נשלחה.")
    except Exception:  # noqa: BLE001 - signing must never fail over a reminder task
        pass

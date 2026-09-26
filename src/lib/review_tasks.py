"""Work the system does for Dror, delivered as a task on the משימות list.

The strategy and the monthly campaign report used to reach Dror by email, with no
way to answer back. Now each is a task on משימות in ``לבדיקה של דרור`` (his
inbox view), linked to the client, and every version is posted the way the
agents post theirs (:mod:`src.automations.clickup_to_claude`): a ``🤖 Claude:``
comment with the link. So the same handles work on it:

* a **reply in the thread** is feedback, and the next version comes back there;
* on a report, the email to the client is a Gmail draft with the PDF, shown as a
  📧 card, and only Dror's ``שלח`` sends it.

A task's kind is its name prefix (as with the signing follow-up,
:mod:`src.lib.reminder_tasks`), so the ``עובד`` field stays empty and the webhook
does not hand the task to an agent on creation. A second run for the same client
(and month) adds a version to the open task instead of opening another.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple, Optional

from . import config, workers


class Kind(NamedTuple):
    key: str
    prefix: str                         # the task name starts with this
    worker: Optional[workers.Worker]    # who revises it (None: its own automation)
    subfolder: str                      # where its Docs go in the client's folder
    explain: str                        # the first comment on a new task


STRATEGY = Kind(
    "strategy", "אסטרטגיה: ", workers.STRATEGIST, "אסטרטגיה",
    "🤖 טיוטת האסטרטגיה של הלקוח. כל גרסה היא Google Doc בתיקיית אסטרטגיה של הלקוח, "
    "עם PDF כאן במשימה.\n"
    "לתיקון: עונים בשרשור מתחת לגרסה (למשל: \"הקהל צעיר יותר, תחדד את הפרסונות\"), "
    "והגרסה הבאה תחזור לאותו שרשור. אפשר גם לערוך את המסמך ישירות.",
)

REPORT = Kind(
    "report", "דוח קמפיינים: ", None, "",
    "🤖 דוח הקמפיינים החודשי של הלקוח. ה-PDF כאן במשימה ובתיקיית הלקוח ב-Drive.\n"
    "לשלוח ללקוח: עונים שלח על כרטיס המייל בשרשור (אפשר לערוך את הטיוטה ב-Gmail קודם).\n"
    "לתיקון הניתוח או ההמלצות: עונים בשרשור מה לשנות, ודוח מעודכן יחזור לשם.",
)

KINDS = (STRATEGY, REPORT)

_MONTH_TAG = re.compile(r"\[month:(\d{4}-\d{2})\]")


def kind_of(task: dict[str, Any]) -> Optional[Kind]:
    name = str(task.get("name") or "")
    for kind in KINDS:
        if name.startswith(kind.prefix):
            return kind
    return None


def enabled() -> bool:
    return bool(config.get("CLICKUP_TASKS_LIST_ID"))


def month_tag(month: str) -> str:
    return f"[month:{month}]"


def month_in(text: str) -> Optional[str]:
    found = _MONTH_TAG.search(text or "")
    return found.group(1) if found else None


def client_field(clickup: Any, list_id: str) -> Optional[str]:
    """The id of the list's ``לקוח`` Relationship field.

    The list has two (``עובד`` points at the סוכנים list), so it is the one that
    points at the clients list, else the one named ``לקוח``; never simply the first,
    which would link the client into ``עובד`` and hand the task to it as an agent.
    """
    from . import crm_fields

    clients_list = str(config.get("CLICKUP_LIST_ID") or "")
    fields = [f for f in clickup.get_list_fields(list_id) if f.get("type") == "list_relationship"]
    for field in fields:
        if clients_list and str((field.get("type_config") or {}).get("subcategory_id")) == clients_list:
            return str(field["id"])
    for field in fields:
        if crm_fields.field_key(str(field.get("name") or "")) == "לקוח":
            return str(field["id"])
    return None


def _closed(task: dict[str, Any]) -> bool:
    return str((task.get("status") or {}).get("type") or "") == "closed"


def open_task(clickup: Any, kind: Kind, client_id: str, name: str,
              description: str) -> str:
    """The open task named ``name``, or a new one linked to the client; its id."""
    list_id = str(config.require("CLICKUP_TASKS_LIST_ID"))
    for task in clickup.list_tasks(list_id):
        if str(task.get("name") or "").strip() == name and not _closed(task):
            return str(task["id"])
    rel = client_field(clickup, list_id)
    made = clickup.create_task(
        list_id, name, description=description,
        custom_fields=[{"id": rel, "value": {"add": [client_id]}}] if rel else None)
    task_id = str(made.get("id") or "")
    if not task_id:
        raise RuntimeError(f"ClickUp did not return an id for the task {name!r}")
    clickup.comment(task_id, kind.explain)
    return task_id


def to_review(clickup: Any, task_id: str) -> None:
    """Best-effort: into Dror's review view. A list without that status keeps its own."""
    try:
        clickup.set_status(task_id, workers.STATUS_REVIEW)
    except Exception:  # noqa: BLE001 - the version is posted; the status is a nicety
        pass


def post_version(clickup: Any, task_id: str, text: str,
                 thread_root: Optional[str] = None) -> str:
    """Post a version: a reply in ``thread_root``'s thread, else a new thread.
    Returns the thread's root comment id (where approval cards go)."""
    if thread_root:
        clickup.reply(thread_root, text)
        return thread_root
    made = clickup.comment(task_id, text) or {}
    return str(made.get("id") or "")

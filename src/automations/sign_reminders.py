"""Chase clients who were sent a contract but haven't signed.

Trigger: scheduled daily (EventBridge).

Dror sends a quote; if the client hasn't signed after **3 days**, this sends them
**one** follow-up with the signing link, then stops (Dror's call, 25.9).

The follow-up is a task on the משימות list (:mod:`src.lib.reminder_tasks`), opened
when the contract went out: its **due date** is when it is sent and its
**description** is the text, so Dror changes either in ClickUp and closing the
task cancels it. It is sent automatically, no approval. A contract sent before
the task existed falls back to the fixed ``sign_reminder`` text at 3 days.

How it knows who to chase: every client currently in secondary status
``נשלחה הצעת מחיר`` (quote sent, not yet signed). Signing moves them to ``חתם``,
so a signed client simply drops out of the set — there is no "mark as done" that
could fail and leave someone chased forever.

How it knows the age: :func:`src.lib.signing.mark_pending` recorded when the quote
went out and how many reminders have been sent. The reminder resends the **same**
signing link (a fresh one issued by :func:`signing.sign_url`), so a client who
lost the first email can sign from the reminder.

Manual/dry-run:
    python -m src.automations.sign_reminders --dry-run
"""

from __future__ import annotations

import time
from typing import Any

from ..lib import emails, signing
from ..lib.clients.crm import SUB_QUOTE_SENT, CrmClient
from .base import Automation, build_arg_parser, run_cli

NAME = "sign_reminders"

DAY = 24 * 60 * 60
# (reminder number, age in days): the schedule Dror asked for (25.9).
SCHEDULE = [(1, 3)]
MAX_REMINDERS = 1


def _due(pending: dict[str, Any], now: float) -> int | None:
    """The highest-numbered reminder that is due and not yet sent, or None.

    Highest-due (not lowest) so a job that missed a day doesn't send reminder #1
    late when the client is already past the #2 threshold — it jumps straight to
    #2 and the client gets one nudge, not two at once.
    """
    sent = int(pending.get("reminders_sent", 0))
    if sent >= MAX_REMINDERS:
        return None
    age_days = (now - int(pending.get("issued_at", now))) / DAY
    due = None
    for number, after_days in SCHEDULE:
        if number > sent and age_days >= after_days:
            due = number
    return due


def run(*, dry_run: bool = False, now: float | None = None) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    now = now if now is not None else time.time()

    clients = crm.list_by_sub_status(SUB_QUOTE_SENT)
    reminded = 0
    for client in clients:
        client_id = str(client["id"])
        to = str(client.get("email") or "").strip()
        pending = signing.get_pending(client_id)
        if not pending:
            # Quote sent before reminders existed, or the record expired. Nothing
            # to base the timing on; leave it rather than guess.
            auto.log_action("no_pending_record", "skipped", client_id=client_id)
            continue

        if pending.get("reminder_task_id"):
            reminded += _from_task(auto, crm, client, pending, to, now, dry_run)
            continue

        number = _due(pending, now)
        if number is None:
            continue
        if not to:
            auto.log_action("no_email", "skipped", client_id=client_id,
                            detail="quote-sent client has no אימייל to remind")
            continue

        try:
            url = signing.sign_url(client_id)
            emails.send_template(
                "sign_reminder", to,
                client_name=client.get("first_name") or client.get("name") or "",
                cta_url=url,
                dry_run=dry_run,
            )
            if not dry_run:
                signing.bump_reminders(client_id, number)
            crm.append_automation_log(
                client_id, f"⏰ תזכורת חתימה #{number} נשלחה ל־{to}")
            auto.log_action("reminder_sent", client_id=client_id,
                            detail=f"reminder #{number} → {to}")
            reminded += 1
        except Exception as exc:  # noqa: BLE001 - one client must not stop the rest
            auto.log_action("reminder_failed", "error", client_id=client_id,
                            detail=str(exc))

    auto.log_action("reminders_done", detail=f"{reminded}/{len(clients)} chased")
    return {"unsigned": len(clients), "reminded": reminded}


def _from_task(auto: Automation, crm: CrmClient, client: dict[str, Any],
               pending: dict[str, Any], to: str, now: float, dry_run: bool) -> int:
    """Send the follow-up the client's task holds, if it is due. Returns 0 or 1."""
    from ..lib import reminder_tasks
    from ..lib.clients.clickup import ClickUpClient

    client_id = str(client["id"])
    if int(pending.get("reminders_sent", 0)) >= MAX_REMINDERS:
        return 0
    clickup = ClickUpClient(dry_run=dry_run)
    task_id = str(pending["reminder_task_id"])
    try:
        task = reminder_tasks.fetch(clickup, task_id)
        if task is None or reminder_tasks.is_closed(task):
            # Dror closed or deleted it: that is his "don't send". Once, then quiet.
            if not dry_run:
                signing.bump_reminders(client_id, MAX_REMINDERS)
            auto.log_action("reminder_cancelled", "skipped", client_id=client_id,
                            detail="המשימה נסגרה או נמחקה, התזכורת לא נשלחה")
            return 0
        due = reminder_tasks.due_ms(task)
        if due is None or now * 1000 < due:
            return 0
        body = reminder_tasks.body_of(task)
        if not to or not body:
            reason = "אין ללקוח מייל" if not to else "תיאור המשימה ריק"
            clickup.comment(task_id, f"❌ התזכורת לא נשלחה: {reason}.")
            if not dry_run:
                signing.bump_reminders(client_id, MAX_REMINDERS)
            auto.log_action("reminder_failed", "error", client_id=client_id, detail=reason)
            return 0
        emails.send_template("sign_reminder", to, body=body,
                             client_name=client.get("first_name") or client.get("name") or "",
                             cta_url=signing.sign_url(client_id), dry_run=dry_run)
        if not dry_run:
            signing.bump_reminders(client_id, 1)
        reminder_tasks.close(clickup, task_id, f"✅ התזכורת נשלחה אל {to}.")
        crm.append_automation_log(client_id, f"⏰ תזכורת חתימה נשלחה ל־{to} (מהמשימה ב-משימות)")
        auto.log_action("reminder_sent", client_id=client_id, detail=f"from task → {to}",
                        url=f"https://app.clickup.com/t/{task_id}")
        return 1
    except Exception as exc:  # noqa: BLE001 - one client must not stop the rest
        auto.log_action("reminder_failed", "error", client_id=client_id, detail=str(exc))
        return 0


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    run_cli(parser, lambda a: run(dry_run=a.dry_run))


if __name__ == "__main__":
    main()

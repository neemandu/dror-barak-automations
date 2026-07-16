"""Chase clients who were sent a contract but haven't signed.

Trigger: scheduled daily (EventBridge).

Dror sends a quote; if the client doesn't sign, this nudges them **twice** — at 2
days and again at 4 days — then stops. Two reminders is a nudge; more is nagging,
and nagging a prospect loses the deal you were trying to close.

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
# (reminder number, age in days) — the schedule Dror asked for.
SCHEDULE = [(1, 2), (2, 4)]
MAX_REMINDERS = 2


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


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    run_cli(parser, lambda a: run(dry_run=a.dry_run))


if __name__ == "__main__":
    main()

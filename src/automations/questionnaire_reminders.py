"""Chase clients who were onboarded but never filled the strategy questionnaire.

Trigger: scheduled daily (EventBridge), alongside the signature reminders.

Onboarding's real deliverable is the questionnaire: its answers become the Google
Doc that seeds the strategy and the social-media prep. Until they arrive, a client
who has already signed and paid is stuck at the start of the engagement — and
before this job, nothing chased them and nothing told Dror.

Two nudges, at 3 and 7 days, then it stops and says so once in the run-log, so the
last word is Dror's rather than an inbox full of reminders. The set to chase is
every client in ``בעבודה`` that still has a pending-questionnaire record;
submitting the form clears the record (see ``src/questionnaire_page.py``), so a
client who answers simply drops out — there is no "mark as done" that could fail
and leave someone chased forever.

Manual/dry-run:
    python -m src.automations.questionnaire_reminders --dry-run
"""

from __future__ import annotations

import time
from typing import Any

from ..lib import emails, signing
from ..lib.clients.crm import SUB_IN_WORK, CrmClient
from .base import Automation, build_arg_parser, run_cli

NAME = "questionnaire_reminders"

DAY = 24 * 60 * 60
# (reminder number, age in days). Slower than the signature chase: the client has
# already committed, so this is a nudge to do homework, not to close a deal.
SCHEDULE = [(1, 3), (2, 7)]
MAX_REMINDERS = 2
# After the last reminder, tell Dror once and stop holding the record.
GIVE_UP_AFTER_DAYS = 10


def _due(pending: dict[str, Any], now: float) -> int | None:
    """The highest-numbered reminder that is due and not yet sent, or None."""
    sent = int(pending.get("reminders_sent", 0))
    if sent >= MAX_REMINDERS:
        return None
    age_days = (now - int(pending.get("issued_at", now))) / DAY
    due = None
    for number, after_days in SCHEDULE:
        if number > sent and age_days >= after_days:
            due = number
    return due


def _age_days(pending: dict[str, Any], now: float) -> float:
    return (now - int(pending.get("issued_at", now))) / DAY


def run(*, dry_run: bool = False, now: float | None = None) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    now = now if now is not None else time.time()

    clients = crm.list_by_sub_status(SUB_IN_WORK)
    reminded = 0
    for client in clients:
        client_id = str(client["id"])
        pending = signing.get_questionnaire_pending(client_id)
        if not pending:
            # Answered already, or onboarded before this job existed. Either way
            # there is nothing to time the chase from.
            continue

        # Past the end of the schedule: stop chasing, and make the silence
        # visible once rather than never.
        if (int(pending.get("reminders_sent", 0)) >= MAX_REMINDERS
                and _age_days(pending, now) >= GIVE_UP_AFTER_DAYS):
            auto.log_action(
                "questionnaire_unanswered", "error", client_id=client_id,
                detail=f"{client.get('name') or client_id} לא מילא/ה את השאלון "
                       f"אחרי {MAX_REMINDERS} תזכורות — כדאי להרים טלפון",
            )
            if not dry_run:
                signing.clear_questionnaire_pending(client_id)
            continue

        number = _due(pending, now)
        if number is None:
            continue

        to = str(client.get("email") or "").strip()
        if not to:
            auto.log_action("no_email", "skipped", client_id=client_id,
                            detail="client owes a questionnaire but has no אימייל")
            continue

        try:
            url = signing.questionnaire_url(client_id)
            emails.send_template(
                "questionnaire_reminder", to,
                client_name=client.get("first_name") or client.get("name") or "",
                cta_url=url,
                dry_run=dry_run,
            )
            if not dry_run:
                signing.bump_questionnaire_reminders(client_id, number)
            crm.append_automation_log(
                client_id, f"⏰ תזכורת למילוי השאלון #{number} נשלחה ל־{to}")
            auto.log_action("reminder_sent", client_id=client_id,
                            detail=f"questionnaire reminder #{number} → {to}")
            reminded += 1
        except Exception as exc:  # noqa: BLE001 - one client must not stop the rest
            auto.log_action("reminder_failed", "error", client_id=client_id,
                            detail=str(exc))

    auto.log_action("questionnaire_reminders_done",
                    detail=f"{reminded}/{len(clients)} chased")
    return {"in_work": len(clients), "reminded": reminded}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    run_cli(parser, lambda a: run(dry_run=a.dry_run))


if __name__ == "__main__":
    main()

"""T2 — Send (or re-send) the strategy questionnaire link.

Trigger: the ``שלח שאלון`` button on the ClickUp task, or the CLI. Onboarding
calls :func:`send_link` right after signing, so there is exactly one place that
knows how a questionnaire goes out.

History: this used to WhatsApp a Google Forms link when a lead reached
``פגישה ראשונית``. The questionnaire is now our own form (``/questionnaire``),
emailed after signing — its answers seed the strategy — so the initial-meeting
trigger is gone, and the button is how Dror re-sends a link a client lost.

Manual/dry-run:
    python -m src.automations.send_questionnaire --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any, Optional

from ..lib.clients.crm import CrmClient
from .base import Automation, build_arg_parser, run_cli

NAME = "send_questionnaire"


def send_link(auto: Automation, crm: CrmClient, client: dict[str, Any],
              *, dry_run: bool) -> Optional[str]:
    """Email the questionnaire link and start the chase clock.

    Returns ``None`` when the mail went out, otherwise *why* it did not — already
    logged and commented on the task, so the caller decides only whether that is
    fatal. Onboarding carries on (the folder and templates are worth keeping);
    the button fails loudly (Dror pressed it and is waiting).
    """
    from ..lib import emails, signing

    client_id = str(client["id"])
    to = str(client.get("email") or "").strip()
    if not to:
        # An error, not a skip: nothing else will chase a questionnaire that
        # never went out. Errors are pinned in the daily email.
        reason = "אין כתובת מייל ללקוח - שאלון האסטרטגיה לא נשלח"
        auto.log_action("no_email", "error", client_id=client_id,
                        detail="client has no אימייל for the questionnaire")
        crm.append_automation_log(
            client_id, f"⚠️ {reason}. ממלאים את שדה המייל ולוחצים שוב על 'שלח שאלון'.")
        return reason
    try:
        url = signing.questionnaire_url(client_id)
        emails.send_template(
            "questionnaire", to,
            client_name=client.get("first_name") or client.get("name") or "",
            cta_url=url, dry_run=dry_run,
        )
        # (Re)start the clock for the chase job. Cleared when the form comes back.
        if not dry_run:
            signing.mark_questionnaire_pending(client_id)
        # Which questionnaire this client got — the admin's "who answered" view,
        # and the form they open keeps meaning this one even if the default changes.
        from ..lib import questionnaire_store

        questionnaire_store.record_sent(
            client_id, str(client.get("name") or client_id),
            questionnaire_store.definition_for_client(client_id)["id"])
        crm.append_automation_log(client_id, f"📋 שאלון האסטרטגיה נשלח ל־{to}")
        auto.log_action("questionnaire_sent", client_id=client_id, detail=to)
        return None
    except Exception as exc:  # noqa: BLE001
        auto.log_action("questionnaire_send_failed", "error", client_id=client_id,
                        detail=str(exc))
        return str(exc)


def run(client_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    """The button / CLI entry: send the link, or raise so the press reports why."""
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    client = crm.get_client(client_id)
    if not str(client.get("email") or "").strip():
        send_link(auto, crm, {**client, "id": client_id}, dry_run=dry_run)  # logs and comments why
        from ..lib.actions import Refused

        raise Refused("אין כתובת מייל ללקוח", commented=True)
    problem = send_link(auto, crm, {**client, "id": client_id}, dry_run=dry_run)
    if problem:
        raise RuntimeError(problem)  # the mail itself failed: worth ClickUp's retry
    return {"sent": True, "to": client.get("email")}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

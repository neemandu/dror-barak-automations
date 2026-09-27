"""T1 — Lead → Google Contacts.

Trigger: a task on the clients list, when it is created **and** whenever it
changes (ClickUp's ``taskUpdated``): Dror usually creates the task with a name
and types the phone a minute later, and a creation-only trigger saw no phone and
never looked again.
Action: save the lead's phone number to Google Contacts so Dror has it on his
phone, and note it in the CRM automation log. Once per (client, phone number):
the many other updates to the task save nothing, and a corrected number is saved
as it is typed.

Manual/dry-run:
    python -m src.automations.lead_to_contacts --client-id 42 --dry-run
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..lib import idempotency
from ..lib.clients.crm import CrmClient
from ..lib.clients.google import GoogleClient
from .base import Automation, build_arg_parser, run_cli

NAME = "lead_to_contacts"

# A saved number is remembered for good: saving it again would only make a
# duplicate contact on Dror's phone.
_REMEMBER_SECONDS = 10 * 365 * 24 * 60 * 60


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def run(client_id: str, *, dry_run: bool = False, client: Optional[dict[str, Any]] = None,
        on_update: bool = False) -> dict[str, Any]:
    """Save the lead's phone to Google Contacts, once per number.

    ``client``: the task as already read (saves a call). ``on_update``: called for
    a task change rather than its creation, so a task with no phone yet is not
    worth a log line; it is simply not saved yet.
    """
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)

    lead = client if client is not None else crm.get_client(client_id)
    phone = str(lead.get("phone") or "").strip()
    if not _digits(phone):
        if not on_update:
            auto.log_action(
                "no_phone", "skipped", client_id=client_id, detail="lead has no phone"
            )
        return {"skipped": "no phone"}

    once = idempotency.guard(NAME, client_id, _digits(phone))
    if not idempotency.claim(once, ttl=_REMEMBER_SECONDS):
        return {"skipped": "this number is already in Google Contacts"}
    try:
        contact = GoogleClient(dry_run=dry_run).create_contact(
            name=lead.get("name", "Lead"), phone=phone, email=lead.get("email") or None
        )
    except Exception:
        idempotency.release(once)  # let the next update try again
        raise
    crm.append_automation_log(client_id, f"Saved phone {phone} to Google Contacts")
    auto.log_action(
        "contact_saved",
        client_id=client_id,
        detail=f"phone={phone}",
        contact=contact.get("resourceName"),
    )
    return {"contact": contact}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM lead/client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

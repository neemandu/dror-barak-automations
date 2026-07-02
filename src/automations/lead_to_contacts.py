"""T1 — Lead → Google Contacts.

Trigger: webhook on a new lead in the CRM.
Action: save the lead's phone number to Google Contacts so Dror has it on his
phone, and note it in the CRM automation log.

Manual/dry-run:
    python -m src.automations.lead_to_contacts --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib.clients.crm import CrmClient
from ..lib.clients.google import GoogleClient
from .base import Automation, build_arg_parser, run_cli

NAME = "lead_to_contacts"


def run(client_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    google = GoogleClient(dry_run=dry_run)

    lead = crm.get_client(client_id)
    phone = lead.get("phone")
    if not phone:
        auto.log_action(
            "no_phone", "skipped", client_id=client_id, detail="lead has no phone"
        )
        return {"skipped": True}

    contact = google.create_contact(
        name=lead.get("name", "Lead"), phone=phone, email=lead.get("email")
    )
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

"""T5 — Onboarding (central module).

Trigger: webhook when the CRM secondary status becomes ``signed``.
Action: everything needed to turn a signed client into a working one, replacing
the manual intake:

  1. Create the client's Drive folder (under the configured parent).
  2. Copy the template files into it.
  3. Create the client in Morning (billing).
  4. Open a WhatsApp channel/group with the client and send a welcome message.
  5. Write the Drive path/URL, Morning client id and status back to the CRM.
  6. Ping Dror (WhatsApp) to confirm which templates to copy.

Steps are independent and each is logged, so a failure in one (e.g. Morning down)
doesn't lose the others — the run-log shows exactly what completed.

Manual/dry-run:
    python -m src.automations.onboarding --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib import config, whatsapp_templates
from ..lib.clients.crm import SUB_IN_WORK, CrmClient
from ..lib.clients.google import GoogleClient
from ..lib.clients.green_api import GreenApiClient
from ..lib.clients.morning import MorningClient
from .base import Automation, build_arg_parser, run_cli

NAME = "onboarding"


def _template_ids() -> list[str]:
    """Template Drive file ids to copy, from ``DRIVE_TEMPLATE_IDS`` (comma-sep)."""
    raw = config.get("DRIVE_TEMPLATE_IDS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def run(client_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    google = GoogleClient(dry_run=dry_run)
    morning = MorningClient(dry_run=dry_run)
    wa = GreenApiClient(dry_run=dry_run)

    client = crm.get_client(client_id)
    name = client.get("name", f"client-{client_id}")
    result: dict[str, Any] = {}

    # 1. Drive folder
    parent = config.get("DRIVE_CLIENTS_PARENT_ID", "clients-root")
    folder = google.create_folder(name, parent)
    result["folder"] = folder
    auto.log_action("drive_folder_created", client_id=client_id, detail=folder.get("webViewLink"))

    # 2. Copy templates
    copied = []
    for tid in _template_ids():
        copied.append(google.copy_file(tid, f"{name} — {tid}", folder["id"]))
    if copied:
        auto.log_action("templates_copied", client_id=client_id, detail=f"{len(copied)} files")

    # 3. Morning client
    morning_client = morning.create_client(
        name, email=client.get("email"), phone=client.get("phone")
    )
    result["morning_client"] = morning_client
    auto.log_action("morning_client_created", client_id=client_id, detail=morning_client.get("id"))

    # 4. WhatsApp channel + welcome
    group = wa.create_group(f"{name} — דרור ברק", [client["phone"]])
    wa.send_message(
        client["phone"],
        whatsapp_templates.render("onboarding_welcome", first_name=client.get("first_name", "")),
    )
    auto.log_action("whatsapp_channel_opened", client_id=client_id, detail=group.get("chatId"))

    # 5. Write results back to CRM
    crm.update_fields(
        client_id,
        drive_folder_url=folder.get("webViewLink"),
        drive_folder_id=folder.get("id"),
        morning_client_id=morning_client.get("id"),
        morning_status="created",
        sub_status=SUB_IN_WORK,
    )
    crm.append_automation_log(client_id, "Onboarding completed (Drive, Morning, WhatsApp)")

    # 6. Prompt Dror to confirm templates
    dror_phone = config.get("DROR_WHATSAPP")
    if dror_phone:
        wa.send_message(
            dror_phone,
            whatsapp_templates.render(
                "onboarding_dror_prompt",
                client_name=name,
                drive_url=folder.get("webViewLink", ""),
            ),
        )
    auto.log_action("onboarding_done", client_id=client_id, detail=name)
    return result


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

"""T5 — Onboarding (central module).

Trigger: webhook when the CRM secondary status becomes ``signed``.
Action: everything needed to turn a signed client into a working one, replacing
the manual intake:

  1. Create the client's Drive folder (under the configured parent).
  2. Copy the template files into it.
  3. Open a WhatsApp channel/group with the client and send a welcome message.
  4. Advance the secondary status to ``in_work``.
  5. Ping Dror (WhatsApp) to confirm which templates to copy.

Steps are independent and each is logged, so a failure in one doesn't lose the
others — the run-log shows exactly what completed.

Manual/dry-run:
    python -m src.automations.onboarding --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib import client_folder, config, whatsapp_templates
from ..lib.clients.crm import SUB_IN_WORK, CrmClient
from ..lib.clients.google import GoogleClient
from ..lib.clients.green_api import GreenApiClient
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
    wa = GreenApiClient(dry_run=dry_run)

    client = crm.get_client(client_id)
    name = client.get("name", f"client-{client_id}")
    result: dict[str, Any] = {}

    # 1. Drive folder — reused if the signing page already made it. Signing runs
    # first (it is what sets `חתם`, which triggers this), so by the time onboarding
    # arrives the folder usually exists and holds the signed contract. Creating a
    # second one would leave Dror with two folders per client and neither complete.
    folder = client_folder.ensure(crm, {**client, "id": client_id}, dry_run=dry_run)
    result["folder"] = folder
    auto.log_action(
        "drive_folder_created" if folder.get("created") else "drive_folder_reused",
        client_id=client_id, url=folder["url"],
        detail="נוצרה תיקייה חדשה" if folder.get("created") else "התיקייה כבר קיימת",
    )

    # 2. Copy templates
    copied = []
    for tid in _template_ids():
        copied.append(google.copy_file(tid, f"{name} — {tid}", folder["id"]))
    if copied:
        auto.log_action("templates_copied", client_id=client_id, detail=f"{len(copied)} files")

    # 3. WhatsApp channel + welcome
    group = wa.create_group(f"{name} — דרור ברק", [client["phone"]])
    wa.send_message(
        client["phone"],
        whatsapp_templates.render("onboarding_welcome", first_name=client.get("first_name", "")),
    )
    auto.log_action("whatsapp_channel_opened", client_id=client_id, detail=group.get("chatId"))

    # 4. Advance the status. client_folder.ensure already recorded the Drive link
    # (it must, or the next run would create a second folder), so it is not
    # repeated here.
    crm.update_fields(client_id, sub_status=SUB_IN_WORK)
    crm.append_automation_log(client_id, "Onboarding completed (Drive, WhatsApp)")

    # 5. Prompt Dror to confirm templates
    dror_phone = config.get("DROR_WHATSAPP")
    if dror_phone:
        wa.send_message(
            dror_phone,
            whatsapp_templates.render(
                "onboarding_dror_prompt",
                client_name=name,
                drive_url=folder["url"],
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

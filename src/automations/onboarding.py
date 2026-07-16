"""T5 — Onboarding (central module).

Trigger: webhook when the CRM secondary status becomes ``signed``.
Action: everything needed to turn a signed client into a working one, replacing
the manual intake:

  1. Create the client's Drive folder (under the configured parent).
  2. Copy the template files into it.
  3. Email the client the strategy questionnaire (our own form).
  4. Advance the secondary status to ``in_work``.

Steps are independent and each is logged, so a failure in one doesn't lose the
others — the run-log shows exactly what completed.

Manual/dry-run:
    python -m src.automations.onboarding --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib import client_folder, config
from ..lib.clients.crm import SUB_IN_WORK, CrmClient
from ..lib.clients.google import GoogleClient
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

    # 3. Email the strategy questionnaire. Its answers become the Google Doc that
    # seeds the whole strategy and feed the last-5-videos analysis, so getting the
    # client to fill it is the real point of onboarding.
    result["questionnaire_sent"] = _send_questionnaire(auto, crm, client, dry_run=dry_run)

    # 4. Advance the status. client_folder.ensure already recorded the Drive link
    # (it must, or the next run would create a second folder), so it is not
    # repeated here.
    crm.update_fields(client_id, sub_status=SUB_IN_WORK)
    auto.log_action("onboarding_done", client_id=client_id, detail=name)
    return result


def _send_questionnaire(auto: Automation, crm: CrmClient, client: dict[str, Any],
                        *, dry_run: bool) -> bool:
    """Email the questionnaire link. Best-effort: a delivery failure must not undo
    the folder and templates already created."""
    from ..lib import emails, signing

    client_id = str(client["id"])
    to = str(client.get("email") or "").strip()
    if not to:
        auto.log_action("no_email", "skipped", client_id=client_id,
                        detail="onboarded client has no אימייל for the questionnaire")
        return False
    try:
        url = signing.questionnaire_url(client_id)
        emails.send_template(
            "questionnaire", to,
            client_name=client.get("first_name") or client.get("name") or "",
            cta_url=url, dry_run=dry_run,
        )
        crm.append_automation_log(client_id, f"📋 שאלון האסטרטגיה נשלח ל־{to}")
        auto.log_action("questionnaire_sent", client_id=client_id, detail=to)
        return True
    except Exception as exc:  # noqa: BLE001
        auto.log_action("questionnaire_send_failed", "error", client_id=client_id,
                        detail=str(exc))
        return False


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

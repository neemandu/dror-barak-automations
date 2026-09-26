"""T5 — Onboarding (central module).

Trigger: webhook when the CRM secondary status becomes ``signed``.
Action: everything needed to turn a signed client into a working one, replacing
the manual intake:

  1. Create the client's Drive folder and its standard subfolders.
  2. Share the folder with the client, and copy in the templates Dror picked
     for them in the ``תבניות`` field (:mod:`client_templates`).
  3. Email the client the strategy questionnaire, and start chasing it.
  4. Check the client has a Meta ad account, which the monthly report needs.
  5. Promote the client to ``active`` / ``in_work`` and summarise on the task.

Steps are independent and each is logged, so a failure in one doesn't lose the
others — the run-log shows exactly what completed. Every step is also safe to
repeat: onboarding is retried after a partial failure, and Drive is asked what
already exists rather than told what we assume we made.

Manual/dry-run:
    python -m src.automations.onboarding --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib import client_folder, config
from ..lib.clients.crm import STATUS_ACTIVE, SUB_IN_WORK, CrmClient
from ..lib.clients.google import GoogleClient
from . import client_templates, send_questionnaire
from .base import Automation, build_arg_parser, run_cli

NAME = "onboarding"


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

    result["subfolders"] = _make_subfolders(auto, crm, google, client_id, folder,
                                            dry_run=dry_run)

    # 2. The client gets their folder (Dror's call, 26.9: the whole folder, from
    # onboarding), then the templates Dror picked for them.
    result["shared_with"] = client_templates.share_folder(
        auto, crm, {**client, "id": client_id}, folder["id"], dry_run=dry_run)
    result["templates"] = _copy_templates(auto, crm, google, {**client, "id": client_id},
                                          folder["id"], dry_run=dry_run)

    # 3. Email the strategy questionnaire. Its answers become the Google Doc that
    # seeds the whole strategy and feed the last-5-videos analysis, so getting the
    # client to fill it is the real point of onboarding. Best-effort: a delivery
    # failure must not undo the folder and templates already created.
    result["questionnaire_sent"] = send_questionnaire.send_link(
        auto, crm, {**client, "id": client_id}, dry_run=dry_run) is None

    # 3b. A WhatsApp welcome — when Dror has a Meta-approved Flow for it. Until
    # then this logs a skip, so the missing step stays visible rather than forgotten.
    result["welcome_flow"] = _send_welcome_flow(auto, {**client, "id": client_id}, dry_run=dry_run)

    # 4. The monthly report reads the ad account from ClickUp, and connecting it is
    # a manual Meta procedure (docs/OPERATIONS.md). Say so now, while Dror is
    # thinking about this client — not in five weeks as a 403 in a failed report.
    result["meta_ad_account"] = _check_meta_account(auto, crm, client)

    # 5. Promote the client. The *primary* status matters as much as the secondary
    # one: `list_active_clients` is what the monthly campaign report iterates, so a
    # client left on `ליד` is one that silently never gets a report.
    crm.update_fields(client_id, status=STATUS_ACTIVE, sub_status=SUB_IN_WORK)
    _summarise(auto, crm, client_id, name, result)
    auto.log_action("onboarding_done", client_id=client_id, detail=name,
                    url=folder["url"])
    return result


def _make_subfolders(auto: Automation, crm: CrmClient, google: GoogleClient,
                     client_id: str, folder: dict[str, str], *,
                     dry_run: bool) -> dict[str, Any]:
    """Give the folder its standard shape, and record where recordings go.

    Best-effort: the folder itself is what onboarding must not lose. A Drive
    hiccup here should not cost the client their questionnaire.
    """
    try:
        subs = client_folder.ensure_subfolders(google, folder["id"], dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        auto.log_action("subfolders_failed", "error", client_id=client_id,
                        detail=str(exc))
        return {}

    made = [n for n, s in subs.items() if s.get("created")]
    if made:
        auto.log_action("subfolders_created", client_id=client_id, url=folder["url"],
                        detail=", ".join(made))

    recordings = subs.get(client_folder.RECORDINGS_SUBFOLDER)
    if recordings:
        crm.update_fields(client_id, recordings_path=recordings["url"])
    return subs


def _copy_templates(auto: Automation, crm: CrmClient, google: GoogleClient,
                    client: dict[str, Any], folder_id: str, *, dry_run: bool) -> dict[str, Any]:
    """The picked templates, copied in. Best-effort: a Drive or ClickUp hiccup here
    must not cost the client their questionnaire."""
    try:
        names = client_templates.picked_for(str(client["id"]), dry_run=dry_run)
        return client_templates.copy_picked(auto, crm, google, client, folder_id, names,
                                            dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        auto.log_action("template_copy_failed", "error", client_id=str(client["id"]),
                        detail=str(exc))
        return {"copied": [], "failed": [str(exc)]}


def _send_welcome_flow(auto: Automation, client: dict[str, Any], *, dry_run: bool) -> bool:
    """Trigger the ManyChat welcome Flow, if one is configured.

    The official API allows only Meta-approved templates for business-initiated
    messages, so the wording lives in ManyChat, not here: ``MANYCHAT_FLOW_ONBOARDING``
    names the Flow. Best-effort — a WhatsApp hiccup must not undo the onboarding.
    """
    from ..lib.clients.manychat import ManyChatClient, to_e164

    client_id = str(client["id"])
    flow = config.get("MANYCHAT_FLOW_ONBOARDING")
    if not flow:
        auto.log_action("no_welcome_flow", "skipped", client_id=client_id,
                        detail="MANYCHAT_FLOW_ONBOARDING not set - no approved welcome template yet")
        return False
    phone = to_e164(str(client.get("phone") or ""), config.get("SMOOVE_DEFAULT_COUNTRY_CODE", "972"))
    if not phone:
        auto.log_action("no_phone_for_welcome", "skipped", client_id=client_id,
                        detail="client has no usable טלפון for the welcome message")
        return False
    try:
        mc = ManyChatClient(dry_run=dry_run)
        subscriber_id, _created = mc.ensure_subscriber(
            phone, client.get("first_name") or client.get("name") or "")
        mc.send_flow(subscriber_id, flow)
        auto.log_action("welcome_flow_sent", client_id=client_id, detail=phone)
        return True
    except Exception as exc:  # noqa: BLE001
        auto.log_action("welcome_flow_failed", "error", client_id=client_id, detail=str(exc))
        return False


def _check_meta_account(auto: Automation, crm: CrmClient, client: dict[str, Any]) -> str:
    """Flag a client with no Meta ad account on the task, while it is cheap to fix."""
    client_id = str(client["id"])
    account = str(client.get("meta_ad_account") or "").strip()
    if account:
        auto.log_action("meta_account_present", client_id=client_id, detail=account)
        return account

    auto.log_action(
        "meta_account_missing", "skipped", client_id=client_id,
        detail="אין חשבון מודעות Meta - הדוח החודשי לא יוכל לרוץ ללקוח הזה",
    )
    crm.append_automation_log(
        client_id,
        "📊 חסר חשבון מודעות Meta במשימה. בלעדיו הדוח החודשי לא ירוץ ללקוח הזה - "
        "ראה נוהל 'חיבור חשבון מודעות Meta של לקוח חדש' ב-docs/OPERATIONS.md.",
    )
    return ""


def _summarise(auto: Automation, crm: CrmClient, client_id: str, name: str,
               result: dict[str, Any]) -> None:
    """Write what onboarding did onto the ClickUp task.

    The run-log already has it, but ClickUp is where Dror looks — a client whose
    task says nothing is one he has to go and check.
    """
    templates = result.get("templates") or {}
    lines = [
        f"✅ האונבורדינג של {name} הושלם",
        f"📁 תיקיית הלקוח: {result['folder']['url']}",
    ]
    copied = len(templates.get("copied") or [])
    if copied:
        lines.append(f"📄 {copied} תבניות הועתקו לתיקייה")
    if templates.get("failed"):
        lines.append("⚠️ העתקת התבניות נכשלה - ראה את הדוח היומי")
    if result.get("shared_with"):
        lines.append(f"🔓 התיקייה שותפה עם {result['shared_with']}")
    if result.get("questionnaire_sent"):
        lines.append("📋 שאלון האסטרטגיה נשלח, ותישלח תזכורת אם לא ימולא")
    lines.append("🚀 הסטטוס עודכן ל'לקוח פעיל' / 'בעבודה'")
    try:
        crm.append_automation_log(client_id, "\n".join(lines))
    except Exception as exc:  # noqa: BLE001 - a comment must not fail the onboarding
        auto.log_action("summary_comment_failed", "error", client_id=client_id,
                        detail=str(exc))


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

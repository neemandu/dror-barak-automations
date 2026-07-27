"""T5 — Onboarding (central module).

Trigger: webhook when the CRM secondary status becomes ``signed``.
Action: everything needed to turn a signed client into a working one, replacing
the manual intake:

  1. Create the client's Drive folder and its standard subfolders.
  2. Copy the template files into it.
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

    result["subfolders"] = _make_subfolders(auto, crm, google, client_id, folder,
                                            dry_run=dry_run)

    # 2. Copy templates
    result["templates"] = _copy_templates(auto, google, client_id, name, folder["id"])

    # 3. Email the strategy questionnaire. Its answers become the Google Doc that
    # seeds the whole strategy and feed the last-5-videos analysis, so getting the
    # client to fill it is the real point of onboarding.
    result["questionnaire_sent"] = _send_questionnaire(auto, crm, client, dry_run=dry_run)

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


def _copy_name(template_name: str, client_name: str) -> str:
    """What a copied template is called in the client's folder.

    The template's own name, then the client's — so the file is recognisable
    both in the folder and once Dror has downloaded or forwarded it.
    """
    return f"{template_name} — {client_name}"


def _copy_templates(auto: Automation, google: GoogleClient, client_id: str,
                    name: str, folder_id: str) -> dict[str, Any]:
    """Copy each configured template in, skipping ones already there.

    Per template rather than all-or-nothing: one unreadable id used to raise out
    of ``run`` with the folder created and the questionnaire never sent.
    """
    ids = _template_ids()
    if not ids:
        # Silence here used to look like success. It is not: the client's folder
        # comes out empty and nobody finds out until Dror opens it.
        auto.log_action("no_templates", "skipped", client_id=client_id,
                        detail="DRIVE_TEMPLATE_IDS is empty — no templates copied")
        return {"copied": [], "skipped": [], "failed": []}

    try:
        present = {str(f.get("name")) for f in google.list_folder(folder_id)}
    except Exception as exc:  # noqa: BLE001 - listing failed; copy blind rather than not at all
        auto.log_action("folder_listing_failed", "error", client_id=client_id,
                        detail=str(exc))
        present = set()

    copied, skipped, failed = [], [], []
    for tid in ids:
        try:
            title = _copy_name(google.file_name(tid), name)
            if title in present:
                skipped.append(title)
                continue
            google.copy_file(tid, title, folder_id)
            copied.append(title)
        except Exception as exc:  # noqa: BLE001 - one bad id must not stop the rest
            failed.append(tid)
            auto.log_action("template_copy_failed", "error", client_id=client_id,
                            detail=f"{tid}: {exc}")

    if copied or skipped:
        detail = f"{len(copied)} הועתקו"
        if skipped:
            detail += f", {len(skipped)} כבר היו בתיקייה"
        auto.log_action("templates_copied", client_id=client_id, detail=detail)
    return {"copied": copied, "skipped": skipped, "failed": failed}


def _send_questionnaire(auto: Automation, crm: CrmClient, client: dict[str, Any],
                        *, dry_run: bool) -> bool:
    """Email the questionnaire link. Best-effort: a delivery failure must not undo
    the folder and templates already created."""
    from ..lib import emails, signing

    client_id = str(client["id"])
    to = str(client.get("email") or "").strip()
    if not to:
        # An error, not a skip: onboarding's whole point is the questionnaire, and
        # nothing else will chase it. Errors are pinned in the daily email.
        auto.log_action("no_email", "error", client_id=client_id,
                        detail="onboarded client has no אימייל for the questionnaire")
        crm.append_automation_log(
            client_id,
            "⚠️ אין כתובת מייל ללקוח — שאלון האסטרטגיה לא נשלח. "
            "מלא/י את שדה המייל והרץ/י את האונבורדינג שוב.",
        )
        return False
    try:
        url = signing.questionnaire_url(client_id)
        emails.send_template(
            "questionnaire", to,
            client_name=client.get("first_name") or client.get("name") or "",
            cta_url=url, dry_run=dry_run,
        )
        # Start the clock for the chase job. Cleared when the form comes back.
        if not dry_run:
            signing.mark_questionnaire_pending(client_id)
        crm.append_automation_log(client_id, f"📋 שאלון האסטרטגיה נשלח ל־{to}")
        auto.log_action("questionnaire_sent", client_id=client_id, detail=to)
        return True
    except Exception as exc:  # noqa: BLE001
        auto.log_action("questionnaire_send_failed", "error", client_id=client_id,
                        detail=str(exc))
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
        detail="אין חשבון מודעות Meta — הדוח החודשי לא יוכל לרוץ ללקוח הזה",
    )
    crm.append_automation_log(
        client_id,
        "📊 חסר חשבון מודעות Meta במשימה. בלעדיו הדוח החודשי לא ירוץ ללקוח הזה — "
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
        lines.append(f"⚠️ {len(templates['failed'])} תבניות נכשלו בהעתקה — ראה את הדוח היומי")
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

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
    # The steps below would each comment on the task. Onboarding says it all once,
    # at the end, in one message (see :func:`_message`).
    quiet = _Held(crm)

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
        auto, quiet, {**client, "id": client_id}, folder["id"], dry_run=dry_run)
    result["templates"] = _copy_templates(auto, quiet, google, {**client, "id": client_id},
                                          folder["id"], dry_run=dry_run)

    # 3. Email the strategy questionnaire. Its answers become the Google Doc that
    # seeds the whole strategy and feed the last-5-videos analysis, so getting the
    # client to fill it is the real point of onboarding. Best-effort: a delivery
    # failure must not undo the folder and templates already created.
    result["questionnaire_problem"] = send_questionnaire.send_link(
        auto, quiet, {**client, "id": client_id}, dry_run=dry_run)
    result["questionnaire_sent"] = result["questionnaire_problem"] is None

    # 3b. A WhatsApp welcome — when Dror has a Meta-approved Flow for it. Until
    # then this logs a skip, so the missing step stays visible rather than forgotten.
    result["welcome_flow"] = _send_welcome_flow(auto, {**client, "id": client_id}, dry_run=dry_run)

    # 4. The monthly report reads the ad account from ClickUp, and connecting it is
    # a manual Meta procedure (docs/OPERATIONS.md). Say so now, while Dror is
    # thinking about this client — not in five weeks as a 403 in a failed report.
    result["meta_ad_account"] = _check_meta_account(auto, quiet, client)

    # 5. Promote the client. The *primary* status matters as much as the secondary
    # one: `list_active_clients` is what the monthly campaign report iterates, so a
    # client left on `ליד` is one that silently never gets a report.
    crm.update_fields(client_id, status=STATUS_ACTIVE, sub_status=SUB_IN_WORK)
    _summarise(auto, crm, {**client, "id": client_id}, name, result, dry_run=dry_run)
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


class _Held:
    """A CRM client that does not comment on the task: the steps onboarding calls
    would each leave a note, and Dror gets one message instead (:func:`_message`)."""

    def __init__(self, crm: CrmClient):
        self._crm = crm

    def append_automation_log(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._crm, name)


def _signed_on_page(client_id: str) -> bool:
    """The client signed on our page (as opposed to Dror moving the status by hand,
    e.g. after a paper signature). The signing page stores the signature before it
    sets `חתם`, so the record is there by the time onboarding runs."""
    from ..lib import contract_store

    try:
        rec = contract_store.get_signed(client_id)
    except Exception:  # noqa: BLE001 - the wording is a nicety, never worth a failed onboarding
        return False
    return bool(rec) and rec.get("status") != contract_store.SUPERSEDED


def _message(client: dict[str, Any], name: str, result: dict[str, Any], *,
             signed_on_page: bool) -> tuple[str, list[str]]:
    """What onboarding did, as the assistant telling Dror: ``(opening, details)``.

    Worded without pronouns for the client: a college is as likely as a person, and
    Hebrew would force a gender on "he signed".
    """
    templates = result.get("templates") or {}
    folder = result["folder"]
    email = str(client.get("email") or "").strip()
    opening = (f"ההסכם עם {name} נחתם, אז סידרתי הכל:" if signed_on_page
               else f"העברת את {name} ל'חתם', אז סידרתי הכל:")

    details = [("פתחתי תיקייה בדרייב, עם התיקיות הקבועות: " if folder.get("created")
                else "תיקיית הלקוח בדרייב מוכנה, עם התיקיות הקבועות: ") + folder["url"]]

    copied = templates.get("copied") or []
    if copied:
        details.append(f"העתקתי לתיקייה {len(copied)} תבניות.")
    elif not templates.get("picked") and not templates.get("failed"):
        details.append("לא סימנת תבניות בשדה 'תבניות', אז לא העתקתי כלום. כשתסמן, הן יועתקו לבד.")
    if templates.get("missing"):
        details.append("לא מצאתי בתיקיית התבניות: " + ", ".join(templates["missing"])
                       + ". השם בשדה 'תבניות' צריך להיות כמו שם הקובץ.")
    if templates.get("failed"):
        details.append("העתקת התבניות נכשלה. הפרטים בדוח היומי.")
    if result.get("shared_with"):
        details.append(f"שיתפתי את התיקייה עם {result['shared_with']}.")

    if result.get("questionnaire_sent"):
        details.append(f"שלחתי אל {email} את שאלון האסטרטגיה. אם לא ימולא, אזכיר בעוד כשלושה ימים "
                       f"ושוב אחרי שבוע, ואם גם אז לא, אעדכן אותך.")
    elif not email:
        details.append("לא שלחתי את שאלון האסטרטגיה כי אין מייל במשימה. אחרי שתוסיף מייל "
                       "אפשר ללחוץ על 'שלח שאלון'.")
    else:
        details.append(f"שליחת שאלון האסטרטגיה נכשלה ({result.get('questionnaire_problem')}). "
                       f"אפשר ללחוץ על 'שלח שאלון' כדי לנסות שוב.")
    if result.get("welcome_flow"):
        details.append("שלחתי הודעת וואטסאפ של ברוכים הבאים.")
    if not result.get("meta_ad_account"):
        details.append("חסר חשבון מודעות Meta במשימה, ובלעדיו הדוח החודשי לא ירוץ. "
                       "הנוהל במדריך המערכת: 'חיבור חשבון מודעות Meta של לקוח חדש'.")
    details.append("עדכנתי את הסטטוס ל'לקוח פעיל' ו'בעבודה'.")
    return opening, details


def _summarise(auto: Automation, crm: CrmClient, client: dict[str, Any], name: str,
               result: dict[str, Any], *, dry_run: bool) -> None:
    """Tell Dror what onboarding did: on the ClickUp task, and by email.

    The run-log already has it, but ClickUp is where Dror looks, and the email is
    for the case where nobody pressed anything: the client signed on their own and
    he would otherwise find out from the daily report.
    """
    client_id = str(client["id"])
    opening, details = _message(client, name, result,
                                signed_on_page=_signed_on_page(client_id))
    body = opening + "\n" + "\n".join("• " + line for line in details)
    try:
        crm.append_automation_log(client_id, "היי,\n" + body + "\nבהצלחה!")
    except Exception as exc:  # noqa: BLE001 - a comment must not fail the onboarding
        auto.log_action("summary_comment_failed", "error", client_id=client_id,
                        detail=str(exc))
    _email_dror(auto, client_id, name, result["folder"]["url"], body, dry_run=dry_run)


def _email_dror(auto: Automation, client_id: str, name: str, folder_url: str, body: str,
                *, dry_run: bool) -> None:
    """The same message by email. Best-effort, and silent without a DROR_EMAIL."""
    to = config.get("DROR_EMAIL")
    if not to:
        return
    try:
        from ..lib import emails

        emails.send_template("onboarding_done", to, client_name=name, summary=body,
                             cta_url=folder_url, dry_run=dry_run)
        auto.log_action("onboarding_email_sent", client_id=client_id, detail=to)
    except Exception as exc:  # noqa: BLE001
        auto.log_action("onboarding_email_failed", "error", client_id=client_id, detail=str(exc))


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

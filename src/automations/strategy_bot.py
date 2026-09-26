"""T8 — Strategy bot.

Trigger: the ``בנה אסטרטגיה`` button on the ClickUp task (run in the
background), or the CLI — once the client has answered the questionnaire.
Action: from the client's questionnaire answers plus a live look at their
digital presence (the social analysis from T3, with real web access), Claude
writes a full marketing strategy. It lands as an editable Google Doc in the
client's ``אסטרטגיה`` folder, posted as the next version on the client's
``אסטרטגיה: <client>`` task on משימות (:mod:`src.lib.review_tasks`), in Dror's
review view: a reply in its thread revises it. Without a משימות list (or if the
task cannot be made) Dror gets an email instead, as before.

Refuses without answers. The strategy used to read ``questionnaire_answers`` from
the CRM record — which is always empty, since answers live in the questionnaire
store — so every strategy was written from the client's name alone.

Manual/dry-run:
    python -m src.automations.strategy_bot --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib import (config, deliverables, emails, questionnaire, questionnaire_store,
                   review_tasks, task_docs, workers)
from ..lib.clients.anthropic_ai import AnthropicClient
from ..lib.clients.clickup import ClickUpClient
from ..lib.clients.crm import CrmClient
from .base import Automation, build_arg_parser, run_cli
from .social_prep import analyze_profiles

NAME = "strategy_bot"

# The brief is the strategist's, so a revision on the task follows the same one.
_SYSTEM = workers.STRATEGIST.job

# The strategy task's description: what a revision on it is asked to do. The
# client's details and questionnaire answers come with it (the task's לקוח).
_BRIEF = ("מסמך אסטרטגיה שיווקית מלא ללקוח, לפי תשובות השאלון שלו והנוכחות הדיגיטלית "
          "שלו. הגרסה הראשונה נכתבה אוטומטית אחרי ניתוח הרשתות; תיקונים מגיעים כתגובות "
          "בשרשור.")


def task_url(task_id: str) -> str:
    return f"https://app.clickup.com/t/{task_id}"


def run(client_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    ai = AnthropicClient(dry_run=dry_run)
    client = {**crm.get_client(client_id), "id": client_id}
    name = str(client.get("name") or client_id)

    response = questionnaire_store.latest_answered(client_id)
    if not response:
        auto.log_action("no_questionnaire", "error", client_id=client_id,
                        detail="אין תשובות לשאלון - אין על מה לבנות אסטרטגיה")
        raise RuntimeError("הלקוח עוד לא מילא את השאלון - אין על מה לבנות אסטרטגיה. "
                           "אפשר לשלוח לו את השאלון שוב.")

    snap, answers = response.get("snapshot") or [], response.get("answers") or {}
    profiles = questionnaire.social_profiles(snap, answers)
    social = analyze_profiles(profiles, ai, focus="strategy") if profiles else {}
    social_text = "\n\n".join(f"### {questionnaire.ROLES.get(r, r)} ({profiles[r]})\n{t}"
                              for r, t in social.items()) or "הלקוח לא מסר קישורים לנוכחות דיגיטלית."
    prompt = (
        f"לקוח: {name}\n"
        + (f"סוג השירות שנמכר ללקוח: {client['service_type']}\n" if client.get("service_type") else "")
        + f"\n# תשובות הלקוח לשאלון ({response.get('questionnaire_title', '')})\n"
        f"{questionnaire.as_text(snap, answers)}\n\n"
        f"# ניתוח הנוכחות הדיגיטלית\n{social_text}\n\n"
        "כתוב עכשיו את מסמך האסטרטגיה המלא."
    )
    strategy = ai.complete(prompt, system=_SYSTEM, max_tokens=16000, thinking=True)

    strategy = task_docs.without_title(strategy)
    task_id = None
    if review_tasks.enabled():
        try:
            task_id, saved = _deliver_as_task(crm, client, strategy, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - the strategy must not be lost: email it
            auto.log_action("review_task_failed", "error", client_id=client_id, detail=str(exc))
            task_id = None
    if task_id is None:
        saved = deliverables.save_markdown_doc(
            crm, client, strategy, file_name=f"אסטרטגיה שיווקית - {name}",
            title="אסטרטגיה שיווקית", subtitle=deliverables.prepared_for(name), dry_run=dry_run)
        _notify_dror(auto, client_id, name, saved["url"], dry_run=dry_run)

    where = f"\nלבדיקה ותיקונים: {task_url(task_id)}" if task_id else ""
    crm.append_automation_log(client_id, f"🤖 טיוטת האסטרטגיה מוכנה לבדיקה\n{saved['url']}{where}")
    auto.log_action(
        "strategy_ready", client_id=client_id, url=saved["url"],
        detail=f"לפי שאלון מ-{str(response.get('answered_at', ''))[:10]}, {len(social)} ערוצים נותחו",
    )
    return {"strategy": strategy, "saved": saved, "task_id": task_id}


def _deliver_as_task(crm: CrmClient, client: dict[str, Any], strategy: str,
                     *, dry_run: bool) -> tuple[str, dict[str, str]]:
    """The strategy as the next version on the client's strategy task in משימות,
    where Dror's replies revise it (:mod:`src.lib.review_tasks`)."""
    from . import clickup_to_claude as bot

    clickup = ClickUpClient(dry_run=dry_run)
    kind = review_tasks.STRATEGY
    name = f"{kind.prefix}{client.get('name') or client['id']}"
    task_id = review_tasks.open_task(clickup, kind, str(client["id"]), name, _BRIEF)
    version = bot.versions(bot.threads(clickup, task_id)) + 1
    saved = task_docs.save(f"{name} - גרסה {version}", strategy, client=client, crm=crm,
                           subfolder=kind.subfolder, dry_run=dry_run)
    review_tasks.post_version(clickup, task_id,
                              bot._comment_body(version, saved["url"], strategy, workers.STRATEGIST.name))
    bot._attach_pdf(clickup, task_id, saved["id"], version, Automation(NAME, dry_run=dry_run), dry_run)
    review_tasks.to_review(clickup, task_id)
    return task_id, saved


def _notify_dror(auto: Automation, client_id: str, client_name: str, url: str,
                 *, dry_run: bool) -> None:
    """Tell Dror the draft is waiting. By email — the WhatsApp digest died with
    Green API, and on the official API every message to him would be a billed,
    Meta-approved template. Best-effort: the strategy is already in Drive and on
    the task, so a mail failure is logged, not fatal."""
    to = config.get("DROR_EMAIL")
    if not to:
        auto.log_action("dror_not_notified", "skipped", client_id=client_id,
                        detail="DROR_EMAIL not set - the draft is in Drive and on the task")
        return
    try:
        emails.send_template("strategy_ready", to, client_name=client_name,
                             cta_url=url, dry_run=dry_run)
        auto.log_action("dror_notified", client_id=client_id, detail=to)
    except Exception as exc:  # noqa: BLE001
        auto.log_action("notify_failed", "error", client_id=client_id, detail=str(exc))


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

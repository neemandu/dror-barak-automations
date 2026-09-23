"""T8 — Strategy bot.

Trigger: the ``בנה אסטרטגיה`` button on the ClickUp task (run in the
background), or the CLI — once the client has answered the questionnaire.
Action: from the client's questionnaire answers plus a live look at their
digital presence (the social analysis from T3, with real web access), Claude
writes a full marketing strategy. It lands as an editable Google Doc in the
client's ``אסטרטגיה`` folder, linked on the task, and Dror gets an email to
review it before it reaches the client.

Refuses without answers. The strategy used to read ``questionnaire_answers`` from
the CRM record — which is always empty, since answers live in the questionnaire
store — so every strategy was written from the client's name alone.

Manual/dry-run:
    python -m src.automations.strategy_bot --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib import config, deliverables, emails, questionnaire, questionnaire_store
from ..lib.clients.anthropic_ai import AnthropicClient
from ..lib.clients.crm import CrmClient
from .base import Automation, build_arg_parser, run_cli
from .social_prep import analyze_profiles

NAME = "strategy_bot"

_SYSTEM = (
    "אתה אסטרטג שיווק בכיר בחברת הייעוץ של דרור ברק, שמלווה מכללות, אקדמיות ויוצרי "
    "קורסים בהגדלת הרשמות: וובינרים, משפכי שיווק, תוכן, וקמפיינים ממומנים במטא. "
    "אתה כותב מסמך אסטרטגיה בעברית, מעשי ומותאם ללקוח הספציפי — לא תבנית כללית.\n\n"
    "מבנה המסמך (כותרות Markdown ברמה 2):\n"
    "1. תקציר מנהלים\n2. קהל היעד והפרסונות\n3. שוק, מתחרים ובידול\n"
    "4. מסר ומיצוב\n5. תוכנית ערוצים ומשפך (כולל וובינר, תוכן וקמפיינים ממומנים)\n"
    "6. תוכנית פעולה ל-90 יום (לפי שבועות או חודשים)\n7. מדדי הצלחה ויעדים\n"
    "8. הנחות ושאלות פתוחות ללקוח\n\n"
    "בסס כל טענה על תשובות השאלון ועל ניתוח הנוכחות הדיגיטלית שקיבלת. כשאתה מניח הנחה "
    "שלא נאמרה — סמן אותה בסעיף 8 ולא כעובדה. אל תמציא מספרים על הלקוח. "
    "זה מסמך, לא שיחה: בלי הקדמה, ובלי שאלה או הצעה להמשך בסוף."
)


def run(client_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    ai = AnthropicClient(dry_run=dry_run)
    client = {**crm.get_client(client_id), "id": client_id}
    name = str(client.get("name") or client_id)

    response = questionnaire_store.latest_answered(client_id)
    if not response:
        auto.log_action("no_questionnaire", "error", client_id=client_id,
                        detail="אין תשובות לשאלון — אין על מה לבנות אסטרטגיה")
        raise RuntimeError("הלקוח עוד לא מילא את השאלון — אין על מה לבנות אסטרטגיה. "
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

    title = f"אסטרטגיה שיווקית — {name}"
    document = f"# {title}\n\n{strategy}"
    saved = deliverables.save_markdown_doc(crm, client, title, document, dry_run=dry_run)

    _notify_dror(auto, client_id, name, saved["url"], dry_run=dry_run)
    crm.append_automation_log(client_id, f"🤖 טיוטת האסטרטגיה מוכנה לבדיקה\n{saved['url']}")
    auto.log_action(
        "strategy_ready", client_id=client_id, url=saved["url"],
        detail=f"לפי שאלון מ-{str(response.get('answered_at', ''))[:10]}, {len(social)} ערוצים נותחו",
    )
    return {"strategy": document, "saved": saved}


def _notify_dror(auto: Automation, client_id: str, client_name: str, url: str,
                 *, dry_run: bool) -> None:
    """Tell Dror the draft is waiting. By email — the WhatsApp digest died with
    Green API, and on the official API every message to him would be a billed,
    Meta-approved template. Best-effort: the strategy is already in Drive and on
    the task, so a mail failure is logged, not fatal."""
    to = config.get("DROR_EMAIL")
    if not to:
        auto.log_action("dror_not_notified", "skipped", client_id=client_id,
                        detail="DROR_EMAIL not set — the draft is in Drive and on the task")
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

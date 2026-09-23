"""T3 — Social-media prep report (AI).

Trigger: the client submits the questionnaire (run in the background), or the
``בנה דוח רשתות`` button.
Action: for each profile link the client gave — found by the question's *role*,
not its wording — Claude opens the page with web fetch/search and writes what it
actually saw: positioning, recent content, three recommendations. The report is a
Google Doc in the client's ``אסטרטגיה`` folder, linked on the ClickUp task.

It says what it could not see. Instagram and TikTok often refuse automated
visitors; an analysis of videos nobody opened would be fiction presented as
research, so the prompt makes the source of every observation explicit.

The per-profile analysis (:func:`analyze_profiles`) is reused by the strategy
bot (T8).

Manual/dry-run:
    python -m src.automations.social_prep --client-id 42 --dry-run
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from ..lib import deliverables, questionnaire, questionnaire_store
from ..lib.clients.anthropic_ai import AnthropicClient
from ..lib.clients.crm import CrmClient
from .base import Automation, build_arg_parser, run_cli

NAME = "social_prep"

_SYSTEM = (
    "אתה אנליסט שיווק דיגיטלי בחברת ייעוץ שעוזרת למכללות, אקדמיות ויוצרי קורסים "
    "להגדיל הרשמות באמצעות וובינרים, משפכי שיווק וקמפיינים ממומנים. אתה כותב בעברית, "
    "קצר ומעשי. אתה מתאר רק מה שראית בפועל: אם דף לא נפתח או שהתוכן חסום, אתה אומר "
    "זאת במפורש ולא משלים מהדמיון."
)

#: The line every analysis opens with. Anything the model writes before it is
#: narration about its own browsing ("I have enough from the site…"), not report.
_SOURCE_LINE = "**מקור המידע:**"

_PURPOSE = {
    "meeting_prep": "הכנה לפגישה עם הלקוח",
    "strategy": "חומר גלם לבניית אסטרטגיה שיווקית",
}


def _prompt(role: str, url: str, focus: str) -> str:
    network = questionnaire.ROLES.get(role, role)
    return (
        f"רשת: {network}\nקישור: {url}\nמטרה: {_PURPOSE.get(focus, focus)}\n\n"
        "פתח את הקישור. אם הדף חסום או דורש התחברות, חפש ברשת מידע ציבורי על החשבון "
        "או על העסק.\n\n"
        "כתוב ב-Markdown, בדיוק במבנה הזה, ופתח ישירות בשורת מקור המידע — בלי הקדמה:\n"
        f"{_SOURCE_LINE} משפט אחד — האם פתחת את הדף, מצאת מידע בחיפוש, או לא הצלחת לגשת.\n"
        "### מיצוב\nפסקה אחת: למי הם מדברים, מה ההבטחה, מה הטון.\n"
        "### התוכן האחרון\nעד 5 פוסטים/סרטונים אחרונים שראית בפועל, שורה לכל אחד. "
        "אם לא ראית תוכן — כתוב שלא ניתן היה לראות, בלי לנחש.\n"
        "### 3 המלצות\nשלוש המלצות קונקרטיות, ממוספרות.\n"
    )


def _from_source_line(text: str) -> str:
    start = text.find(_SOURCE_LINE)
    return text[start:] if start > 0 else text


def analyze_profiles(
    profiles: dict[str, str],
    ai: AnthropicClient,
    *,
    focus: str = "meeting_prep",
) -> dict[str, str]:
    """``{role: analysis markdown}`` — one web-enabled Claude call per profile, in parallel."""
    items = [(role, url) for role, url in profiles.items() if url]
    if not items:
        return {}

    def one(item: tuple[str, str]) -> tuple[str, str]:
        role, url = item
        return role, _from_source_line(ai.complete(_prompt(role, url, focus), system=_SYSTEM,
                                                   max_tokens=3000, web=True))

    with ThreadPoolExecutor(max_workers=min(4, len(items))) as pool:
        return dict(pool.map(one, items))


def _compile_report(name: str, profiles: dict[str, str], analyses: dict[str, str]) -> str:
    lines = [f"# דוח הכנה — נוכחות דיגיטלית · {name}", ""]
    for role, text in analyses.items():
        lines += [f"## {questionnaire.ROLES.get(role, role)}", f"קישור: {profiles.get(role, '')}", "", text, ""]
    return "\n".join(lines)


def profiles_for(client_id: str) -> dict[str, str]:
    """The profile links from the client's latest submitted questionnaire."""
    response = questionnaire_store.latest_answered(client_id)
    if not response:
        return {}
    return questionnaire.social_profiles(response.get("snapshot") or [], response.get("answers") or {})


def run(
    client_id: str,
    *,
    dry_run: bool = False,
    profiles: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    client = {**crm.get_client(client_id), "id": client_id}
    name = str(client.get("name") or client_id)

    if profiles is None:
        profiles = profiles_for(client_id)
    if not profiles:
        auto.log_action("no_profiles", "skipped", client_id=client_id,
                        detail="בשאלון לא מולאו קישורים לרשתות — אין מה לנתח")
        crm.append_automation_log(
            client_id, "ℹ️ דוח רשתות לא נבנה: בשאלון לא מולאו קישורים לרשתות חברתיות או לאתר.")
        return {"report": "", "analyses": {}, "saved": {}}

    analyses = analyze_profiles(profiles, AnthropicClient(dry_run=dry_run))
    report = _compile_report(name, profiles, analyses)
    saved = deliverables.save_markdown_doc(crm, client, f"דוח הכנה לרשתות — {name}", report,
                                           dry_run=dry_run)
    crm.append_automation_log(
        client_id, f"🔎 דוח ההכנה לרשתות מוכן ({len(analyses)} ערוצים)\n{saved['url']}")
    auto.log_action("prep_report_ready", client_id=client_id,
                    detail=f"{len(analyses)} ערוצים נותחו", url=saved["url"])
    return {"report": report, "saved": saved, "analyses": analyses}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

"""T8 — Strategy bot.

Trigger: manual (invoked by Dror, e.g. via a Claude Code skill) once a client's
questionnaire is in.
Action: from the questionnaire answers, analyze target audience + competitors +
digital presence (reusing the social-profile analysis from T3), produce a full
strategy, inject it into Dror's strategy template, save it to the client's Drive
folder, and notify Dror to review before it reaches the client.

Per the proposal, only the *strategy authoring* part is built here; the social
profile analysis is reused from :mod:`src.automations.social_prep`.

Manual/dry-run:
    python -m src.automations.strategy_bot --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib import config
from ..lib.clients.anthropic_ai import AnthropicClient
from ..lib.clients.crm import CrmClient
from ..lib.clients.google import GoogleClient
from ..lib.clients.green_api import GreenApiClient
from .base import Automation, build_arg_parser, run_cli
from .social_prep import analyze_profiles

NAME = "strategy_bot"

_SYSTEM = (
    "You are Dror Barak's strategy assistant. Using the client's questionnaire "
    "answers and social analysis, produce a full marketing strategy in Hebrew for "
    "a college looking to enrol more students via webinars and funnels. Cover: "
    "target audience, competitor landscape, positioning, channel plan, and a "
    "90-day action plan."
)


def run(client_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    google = GoogleClient(dry_run=dry_run)
    ai = AnthropicClient(dry_run=dry_run)

    client = crm.get_client(client_id)
    answers = client.get("questionnaire_answers", {})
    profiles = client.get("social_profiles", {})

    # Reuse T3's social analysis as strategy input.
    social = analyze_profiles(profiles, ai, focus="strategy")

    prompt = (
        f"Questionnaire answers:\n{answers}\n\n"
        f"Social analysis:\n{social}\n\n"
        "Write the full strategy document now."
    )
    strategy = ai.complete(prompt, system=_SYSTEM, max_tokens=4000)

    document = "\n".join(
        [f"# אסטרטגיה שיווקית — {client.get('name','')}", "", strategy]
    )
    folder_id = client.get("drive_folder_id") or config.get("DRIVE_DEFAULT_PARENT_ID")
    saved: dict[str, Any] = {}
    if folder_id:
        saved = google.upload_file(
            name=f"strategy_{client_id}.md",
            content=document.encode("utf-8"),
            parent_id=folder_id,
            mime_type="text/markdown",
        )

    dror_phone = config.get("DROR_WHATSAPP")
    if dror_phone:
        GreenApiClient(dry_run=dry_run).send_message(
            dror_phone,
            f"אסטרטגיה מוכנה לבדיקה — {client.get('name','')}.\n"
            f"{saved.get('webViewLink','(נשמר בדרייב)')}",
        )
    crm.append_automation_log(client_id, "Strategy drafted (awaiting Dror's review)")
    auto.log_action(
        "strategy_ready",
        client_id=client_id,
        detail=f"{len(social)} profiles used",
        report_url=saved.get("webViewLink"),
    )
    return {"strategy": document, "saved": saved}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

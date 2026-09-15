"""T8 — Strategy bot.

Trigger: manual — the ``בנה אסטרטגיה`` button on the ClickUp task, or the CLI —
once a client's questionnaire is in.
Action: from the questionnaire answers, analyze target audience + competitors +
digital presence (reusing the social-profile analysis from T3), produce a full
strategy, inject it into Dror's strategy template, save it to the client's Drive
folder, and email Dror to review before it reaches the client.

Per the proposal, only the *strategy authoring* part is built here; the social
profile analysis is reused from :mod:`src.automations.social_prep`.

Manual/dry-run:
    python -m src.automations.strategy_bot --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib import client_folder, config, emails
from ..lib.clients.anthropic_ai import AnthropicClient
from ..lib.clients.crm import CrmClient
from ..lib.clients.google import GoogleClient
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
    # The client's own folder, created if needed — not a key get_client never
    # returns (the old drive_folder_id), which silently sent every strategy to the
    # shared default parent.
    folder = client_folder.ensure(crm, {**client, "id": client_id}, dry_run=dry_run)
    saved = google.upload_file(
        name=f"strategy_{client_id}.md",
        content=document.encode("utf-8"),
        parent_id=folder["id"],
        mime_type="text/markdown",
    )

    _notify_dror(auto, client_id, client.get("name", ""),
                 saved.get("webViewLink") or folder["url"], dry_run=dry_run)
    crm.append_automation_log(client_id, "Strategy drafted (awaiting Dror's review)")
    auto.log_action(
        "strategy_ready",
        client_id=client_id,
        detail=f"{len(social)} profiles used",
        url=saved.get("webViewLink") or folder["url"],
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

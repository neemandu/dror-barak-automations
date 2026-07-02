"""T3 — Social-media prep report (AI).

Trigger: webhook on questionnaire (Google Forms) submit, or manual.
Action: for each social profile the client listed, ask Claude to analyze the
profile and its last ~5 videos and produce a prep report (profile link, summary,
recommendations) so Dror walks into the meeting prepared. Saves the report to the
client's Drive folder (when known) and notes it in the CRM.

The profile-analysis helper (:func:`analyze_profiles`) is reused by the strategy
bot (T8), matching the proposal note that the social analysis built here is
shared.

Manual/dry-run:
    python -m src.automations.social_prep --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any, Optional

from ..lib import config
from ..lib.clients.anthropic_ai import AnthropicClient
from ..lib.clients.crm import CrmClient
from ..lib.clients.google import GoogleClient
from .base import Automation, build_arg_parser, run_cli

NAME = "social_prep"

_SYSTEM = (
    "You are a marketing analyst for a consulting agency that helps colleges "
    "enrol more students. Analyze the given social profile and its recent videos "
    "and produce concise, actionable notes for a sales/strategy meeting."
)


def analyze_profiles(
    profiles: dict[str, str],
    ai: AnthropicClient,
    *,
    focus: str = "meeting_prep",
) -> dict[str, str]:
    """Return ``{network: analysis_text}`` for each profile URL.

    ``focus`` tunes the prompt: ``meeting_prep`` (T3) or ``strategy`` (T8).
    """
    out: dict[str, str] = {}
    for network, url in profiles.items():
        if not url:
            continue
        prompt = (
            f"Network: {network}\nProfile: {url}\n"
            f"Purpose: {focus}.\n"
            "Give: (1) a one-paragraph summary of the profile's positioning, "
            "(2) observations from the last 5 videos/posts, "
            "(3) 3 concrete recommendations."
        )
        out[network] = ai.complete(prompt, system=_SYSTEM, max_tokens=1200)
    return out


def _compile_report(client: dict[str, Any], analyses: dict[str, str]) -> str:
    lines = [f"# דוח הכנה לפגישה — {client.get('name', '')}", ""]
    for network, text in analyses.items():
        url = client.get("social_profiles", {}).get(network, "")
        lines += [f"## {network}", f"פרופיל: {url}", "", text, ""]
    if not analyses:
        lines.append("_לא נמצאו פרופילים חברתיים בשאלון._")
    return "\n".join(lines)


def run(
    client_id: str,
    *,
    dry_run: bool = False,
    profiles: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    google = GoogleClient(dry_run=dry_run)
    ai = AnthropicClient(dry_run=dry_run)

    client = crm.get_client(client_id)
    profiles = profiles or client.get("social_profiles", {})
    analyses = analyze_profiles(profiles, ai, focus="meeting_prep")
    report = _compile_report(client, analyses)

    saved: dict[str, Any] = {}
    folder_id = client.get("drive_folder_id") or config.get("DRIVE_DEFAULT_PARENT_ID")
    if folder_id:
        saved = google.upload_file(
            name=f"prep_{client_id}.md",
            content=report.encode("utf-8"),
            parent_id=folder_id,
            mime_type="text/markdown",
        )
    crm.append_automation_log(
        client_id, f"Generated social prep report ({len(analyses)} profiles)"
    )
    auto.log_action(
        "prep_report_ready",
        client_id=client_id,
        detail=f"{len(analyses)} profiles analyzed",
        report_url=saved.get("webViewLink"),
    )
    return {"report": report, "saved": saved, "analyses": analyses}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

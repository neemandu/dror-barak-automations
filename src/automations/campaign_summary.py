"""T7 — Monthly campaign summary.

Trigger: scheduled at month end (or manual per client).
Action: analyze the month's campaign results, produce a report in Dror's template
with AI recommendations, save it to the client's Drive folder, and send it to Dror
for approval before he forwards it to the client.

The campaign metrics source is not yet confirmed (Open Question #9). This module
takes metrics as an argument and, when none are supplied, uses a documented
placeholder so the report/AI pipeline is fully exercised in dry-run.

Manual/dry-run:
    python -m src.automations.campaign_summary --client-id 42 --dry-run
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from ..lib import config
from ..lib.clients.anthropic_ai import AnthropicClient
from ..lib.clients.crm import CrmClient
from ..lib.clients.google import GoogleClient
from ..lib.clients.green_api import GreenApiClient
from .base import Automation, build_arg_parser, run_cli

NAME = "campaign_summary"

_PLACEHOLDER_METRICS = {
    "spend": 8200,
    "impressions": 412000,
    "clicks": 9300,
    "leads": 214,
    "cost_per_lead": 38.3,
    "note": "placeholder metrics — replace with real data source (Open Question #9)",
}

_SYSTEM = (
    "You are a paid-media analyst. Given a month's campaign metrics for a college "
    "lead-generation account, write a short Hebrew performance summary and 3-5 "
    "concrete, prioritized recommendations for next month."
)


def run(
    client_id: str,
    *,
    dry_run: bool = False,
    metrics: Optional[dict[str, Any]] = None,
    month: Optional[str] = None,
) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    google = GoogleClient(dry_run=dry_run)
    ai = AnthropicClient(dry_run=dry_run)

    client = crm.get_client(client_id)
    month = month or date.today().strftime("%Y-%m")
    metrics = metrics or _PLACEHOLDER_METRICS

    analysis = ai.complete(
        f"Metrics for {month}:\n{json.dumps(metrics, ensure_ascii=False, indent=2)}",
        system=_SYSTEM,
        max_tokens=1500,
    )
    report = "\n".join(
        [
            f"# סיכום קמפיינים — {client.get('name','')} — {month}",
            "",
            "## נתונים",
            json.dumps(metrics, ensure_ascii=False, indent=2),
            "",
            "## ניתוח והמלצות (AI)",
            analysis,
        ]
    )

    folder_id = client.get("drive_folder_id") or config.get("DRIVE_DEFAULT_PARENT_ID")
    saved: dict[str, Any] = {}
    if folder_id:
        saved = google.upload_file(
            name=f"campaign_summary_{client_id}_{month}.md",
            content=report.encode("utf-8"),
            parent_id=folder_id,
            mime_type="text/markdown",
        )

    # Send to Dror for approval before he forwards it to the client.
    dror_phone = config.get("DROR_WHATSAPP")
    if dror_phone:
        GreenApiClient(dry_run=dry_run).send_message(
            dror_phone,
            f"סיכום קמפיינים מוכן לאישור — {client.get('name','')} ({month}).\n"
            f"{saved.get('webViewLink','(נשמר בדרייב)')}",
        )
    crm.append_automation_log(client_id, f"Campaign summary drafted for {month} (awaiting approval)")
    auto.log_action(
        "campaign_summary_ready",
        client_id=client_id,
        detail=f"{month}",
        report_url=saved.get("webViewLink"),
    )
    return {"report": report, "saved": saved}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    parser.add_argument("--month", help="Month label, e.g. 2026-06 (default: current)")
    parser.add_argument("--metrics-json", help="Path to a JSON file of campaign metrics")

    def handler(a: Any) -> Any:
        metrics = None
        if a.metrics_json:
            with open(a.metrics_json, encoding="utf-8") as fh:
                metrics = json.load(fh)
        return run(a.client_id, dry_run=a.dry_run, metrics=metrics, month=a.month)

    run_cli(parser, handler)


if __name__ == "__main__":
    main()

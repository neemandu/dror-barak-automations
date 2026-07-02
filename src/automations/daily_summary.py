"""T10 — Daily summary to Dror.

Trigger: scheduled at end of day.
Action: read the run-log written by every automation during the day, build a short
summary (counts per automation and per status, plus any errors), and send it to
Dror on WhatsApp so he always knows what the automations did — "לא נשאר באפלה".

Manual/dry-run:
    python -m src.automations.daily_summary --dry-run
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timezone
from typing import Any, Optional

from ..lib import config, run_log, whatsapp_templates
from ..lib.clients.green_api import GreenApiClient
from .base import Automation, build_arg_parser, run_cli

NAME = "daily_summary"


def _start_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


def build_summary(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "אין פעילות אוטומציות להיום."
    per_automation: Counter = Counter(e.get("automation", "?") for e in entries)
    per_status: Counter = Counter(e.get("status", "?") for e in entries)
    errors = [e for e in entries if e.get("status") == "error"]

    lines = [f"סה\"כ פעולות: {len(entries)}", ""]
    lines.append("לפי אוטומציה:")
    for name, count in per_automation.most_common():
        lines.append(f"  • {name}: {count}")
    lines.append("")
    lines.append(
        "סטטוסים: " + ", ".join(f"{s}={c}" for s, c in per_status.most_common())
    )
    if errors:
        lines.append("")
        lines.append("⚠️ שגיאות:")
        for e in errors[:10]:
            lines.append(f"  • {e.get('automation')}: {e.get('detail')}")
    return "\n".join(lines)


def run(*, dry_run: bool = False, since: Optional[datetime] = None) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    since = since or _start_of_today_utc()
    entries = run_log.read_since(since)
    body = build_summary(entries)
    message = whatsapp_templates.render(
        "daily_summary",
        date=since.date().isoformat(),
        body=body,
    )

    dror_phone = config.get("DROR_WHATSAPP")
    sent = None
    if dror_phone:
        sent = GreenApiClient(dry_run=dry_run).send_message(dror_phone, message)
    else:
        auto.log_action(
            "no_recipient", "skipped", detail="DROR_WHATSAPP not set; printing only"
        )
    auto.log_action("summary_sent", detail=f"{len(entries)} entries", message=message[:80])
    return {"message": message, "entries": len(entries), "sent": sent}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    run_cli(parser, lambda a: run(dry_run=a.dry_run))


if __name__ == "__main__":
    main()

"""T7 — Monthly campaign summary.

Trigger: scheduled on the 1st of each month (or manual per client, or the ClickUp
``בנה דוח קמפיין`` button). Action: pull last month's real campaign numbers from
the Meta Ads API for the client's ad account, lay them into Dror's report template
with AI recommendations, save the PDF to the client's Drive folder, and email it to
Dror for approval before he forwards it to the client.

**Which ad account** comes from the ``חשבון מודעות Meta`` field on the client's
ClickUp task — one account per client. There is deliberately **no global fallback**:
defaulting to a shared account would put one client's spend into another client's
PDF, and it would look entirely normal on the way out. A client with no account set
is skipped (:class:`NoAdAccount`), never defaulted.

**Which month:** the previous, completed month by default — a summary of a
half-finished month is not a summary. ``--month 2026-06`` overrides.

Manual/dry-run:
    python -m src.automations.campaign_summary --client-id 42 --dry-run
    python -m src.automations.campaign_summary --all --dry-run
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..lib import (
    campaign_metrics,
    campaign_report,
    client_folder,
    config,
    emails,
    pdf,
)
from ..lib.clients.anthropic_ai import AnthropicClient
from ..lib.clients.crm import CrmClient
from ..lib.clients.meta_ads import MetaAdsClient
from .base import Automation, build_arg_parser, run_cli

NAME = "campaign_summary"

# Hebrew tokenizes poorly and the model returns a summary PLUS 3–5 recommendations;
# 1500 truncated it mid-recommendation, and complete() never checks stop_reason —
# so a cut-off line reached Dror silently. 8000 is comfortable headroom.
_MAX_TOKENS = 8000

_SYSTEM = (
    "You are a paid-media analyst writing the monthly campaign report for a client "
    "of Dror Barak, a consultancy that runs lead-generation campaigns for Israeli "
    "colleges and academies. The reader is Dror, who forwards the report to the "
    "client.\n\n"
    "Write in Hebrew. All money is Israeli shekels (₪).\n\n"
    "Return exactly two sections, separated by a line containing only `---`:\n"
    "1. A short performance summary (3-5 sentences): what happened this month and "
    "why it matters. State the numbers you are drawing on.\n"
    "2. 3-5 concrete, prioritized recommendations for next month. Each on its own "
    "line, starting with a number. Say what to do and what you expect it to change. "
    "Ground every recommendation in a number from the data.\n\n"
    "Rules:\n"
    "- Use only the metrics given. Never invent a number, a benchmark, or a "
    "month-over-month comparison you were not given.\n"
    "- \"עלות לליד: —\" means no leads were recorded, not that leads were free. "
    "Zero spend means the campaigns did not run — say so plainly; that is the "
    "finding.\n"
    "- No preamble, no headings, no markdown bold. Plain prose and numbered lines."
)


class NoAdAccount(ValueError):
    """The client has no ``חשבון מודעות Meta``. A clean skip, not a failure.

    Raised (not returned) so the ClickUp button path fails visibly: handle_action
    posts its green confirmation on any non-exception return, which would tell Dror
    a report exists when none does.
    """


def _analysis(summary: dict[str, Any], client: dict[str, Any], account: dict[str, Any],
              month: str, *, dry_run: bool) -> tuple[str, str]:
    """Claude's ``(analysis, recommendations)`` for the month's metrics."""
    ai = AnthropicClient(dry_run=dry_run)
    user = "\n".join([
        f"לקוח: {client.get('name','')}",
        f"חשבון מודעות: {account.get('name','')}",
        f"חודש: {campaign_metrics.month_label_he(month)}",
        "",
        'סה"כ:',
        json.dumps(summary.get("totals", {}), ensure_ascii=False, indent=2),
        "",
        "לפי קמפיין:",
        json.dumps(summary.get("campaigns", []), ensure_ascii=False, indent=2),
    ])
    text = ai.complete(user, system=_SYSTEM, max_tokens=_MAX_TOKENS)
    analysis, sep, recommendations = text.partition("\n---\n")
    if not sep:
        # A missing separator must degrade, never lose the prose: the metrics table
        # is the report's spine and is correct without it.
        return text.strip(), ""
    return analysis.strip(), recommendations.strip()


def run(
    client_id: str,
    *,
    dry_run: bool = False,
    month: Optional[str] = None,
    metrics: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build one client's report for one month. Raises :class:`NoAdAccount` to skip."""
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)

    client = crm.get_client(client_id)
    client = {**client, "id": client_id}
    month = month or campaign_metrics.previous_month()
    label = campaign_metrics.month_label_he(month)

    account_id = str(client.get("meta_ad_account") or "").strip()
    if not account_id:
        auto.log_action(
            "no_ad_account", "skipped", client_id=client_id,
            detail="לא הוגדר חשבון מודעות Meta על כרטיס הלקוח",
        )
        raise NoAdAccount(f"client {client_id} has no חשבון מודעות Meta")

    # Real Meta numbers, unless a caller supplied metrics (--metrics-json / tests).
    meta = MetaAdsClient(dry_run=dry_run)
    account = meta.account(account_id)
    if metrics is not None:
        summary = campaign_metrics.summarize(
            metrics.get("rows", []) if isinstance(metrics, dict) else metrics,
            currency=account.get("currency") or "ILS",
        )
    else:
        since, until = campaign_metrics.month_range(month)
        rows = meta.insights(account_id, since=since, until=until)
        summary = campaign_metrics.summarize(rows, currency=account.get("currency") or "ILS")

    analysis, recommendations = _analysis(summary, client, account, month, dry_run=dry_run)
    fields = campaign_report.fields_from(
        client, account, summary, month=month,
        analysis=analysis, recommendations=recommendations,
    )
    document = campaign_report.build_document(campaign_report.render(fields))

    folder = client_folder.ensure(crm, client, dry_run=dry_run)
    totals = summary.get("totals", {})
    symbol = summary.get("symbol", "")

    drive_url = folder["url"]
    pdf_bytes = b""
    if not dry_run:
        # pdf.* is not dry-run aware — it calls Google directly — so the whole
        # PDF/upload/send tail is guarded, exactly as sign_page does.
        pdf_bytes = pdf.html_to_pdf(document, name=f"campaign_report_{client_id}_{month}")
        saved = pdf.upload_pdf(pdf_bytes, f"דוח קמפיינים — {label}.pdf", folder["id"])
        drive_url = saved.get("webViewLink") or drive_url

    # Send to Dror for approval. Best-effort: the report is in Drive either way, and
    # a mail failure must not lose the work.
    dror_email = str(config.get("DROR_EMAIL") or "").strip()
    if dror_email:
        try:
            attachments = (
                [emails.Attachment(f"campaign_report_{month}.pdf", pdf_bytes)]
                if pdf_bytes else None
            )
            emails.send_template(
                "campaign_report_ready", dror_email,
                attachments=attachments,
                dry_run=dry_run,
                client_name=client.get("name") or "",
                month_label=label,
                spend=campaign_report._money(totals.get("spend"), symbol),
                leads=campaign_report._int(totals.get("leads")),
                cost_per_lead=campaign_report._rate(totals.get("cost_per_lead"), symbol),
                cta_url=drive_url,
            )
        except Exception as exc:  # noqa: BLE001 - report is saved; delivery is best-effort
            auto.log_action("approval_email_failed", "error", client_id=client_id,
                            detail=str(exc))

    crm.append_automation_log(
        client_id, f"📊 דוח קמפיינים ל־{label} מוכן לאישור\n{drive_url}")
    auto.log_action(
        "campaign_summary_ready", client_id=client_id, url=drive_url,
        detail=f"{label} · {campaign_report._money(totals.get('spend'), symbol)} · "
               f"{totals.get('leads', 0)} לידים",
    )
    return {"month": month, "summary": summary, "url": drive_url,
            "report": document}


def run_all(*, dry_run: bool = False, month: Optional[str] = None) -> dict[str, Any]:
    """Build a report for every active client. One client's failure never stops the rest."""
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    clients = crm.list_active_clients()

    built = skipped = failed = 0
    for client in clients:
        client_id = str(client.get("id") or "")
        try:
            run(client_id, dry_run=dry_run, month=month)
            built += 1
        except NoAdAccount:
            # Expected and already logged as "skipped" inside run(); nothing wrong.
            skipped += 1
        except Exception as exc:  # noqa: BLE001 - one client must not stop the rest
            failed += 1
            auto.log_action("report_failed", "error", client_id=client_id, detail=str(exc))

    auto.log_action(
        "campaign_reports_done",
        detail=f"{built}/{len(clients)} built, {skipped} skipped, {failed} failed",
    )
    return {"clients": len(clients), "built": built, "skipped": skipped, "failed": failed}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", help="CRM client id")
    parser.add_argument("--all", action="store_true", help="Every active client")
    parser.add_argument("--month", help="Month label, e.g. 2026-06 (default: previous month)")
    parser.add_argument("--metrics-json", help="Path to a JSON file of insights rows")

    def handler(a: Any) -> Any:
        if a.all:
            return run_all(dry_run=a.dry_run, month=a.month)
        if not a.client_id:
            parser.error("one of --client-id or --all is required")
        metrics = None
        if a.metrics_json:
            with open(a.metrics_json, encoding="utf-8") as fh:
                metrics = json.load(fh)
        return run(a.client_id, dry_run=a.dry_run, metrics=metrics, month=a.month)

    run_cli(parser, handler)


if __name__ == "__main__":
    main()

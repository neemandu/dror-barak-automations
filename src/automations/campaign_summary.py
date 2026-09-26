"""T7 — Monthly campaign summary.

Trigger: scheduled on the 1st of each month (or manual per client, or the ClickUp
``בנה דוח קמפיין`` button). Action: pull last month's real campaign numbers from
the Meta Ads API for the client's ad account, lay them into Dror's report template
with AI recommendations, save the PDF to the client's Drive folder, and hand it to
Dror for approval as a task on משימות (:mod:`src.lib.review_tasks`): ``דוח קמפיינים:
<client> · <month>``, the PDF attached, and the email to the client as a Gmail draft
behind a 📧 card, sent only by Dror's ``שלח``. A reply in the thread rebuilds the
analysis and recommendations with his note (:func:`revise`). Without a משימות list
the PDF is emailed to Dror instead, as before.

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
from typing import Any, NamedTuple, Optional

from ..lib import (
    campaign_metrics,
    campaign_report,
    client_folder,
    config,
    emails,
    pdf,
    pdf_chromium,
    review_tasks,
)
from ..lib.clients.anthropic_ai import AnthropicClient
from ..lib.clients.crm import CrmClient
from ..lib.clients.meta_ads import MetaAdsClient
from .base import Automation, build_arg_parser, run_cli

NAME = "campaign_summary"

# Hebrew tokenizes poorly and the model returns a summary PLUS 3–5 recommendations;
# 1500 truncated it mid-recommendation, and complete() never checks stop_reason —
# so a cut-off line reached Dror silently. 8000 is comfortable headroom.
# Thinking counts toward max_tokens even when its text is not returned.
_MAX_TOKENS = 16000

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
    "- \"עלות לליד: -\" means no leads were recorded, not that leads were free. "
    "Zero spend means the campaigns did not run - say so plainly; that is the "
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
              month: str, *, dry_run: bool, feedback: Optional[str] = None,
              previous: Optional[str] = None) -> tuple[str, str]:
    """Claude's ``(analysis, recommendations)`` for the month's metrics; with
    ``feedback``, a rewrite of ``previous`` (the last version) by Dror's note."""
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
    if feedback:
        user += ("\n\nהגרסה הקודמת של הדוח:\n" + (previous or "(לא נשמרה)")
                 + "\n\nההערות של דרור עליה:\n" + feedback
                 + "\n\nכתוב מחדש את שני החלקים לפי ההערות, באותו מבנה.")
    text = ai.complete(user, system=_SYSTEM, max_tokens=_MAX_TOKENS, thinking=True)
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
    feedback: Optional[str] = None,
    previous: Optional[str] = None,
    task_id: Optional[str] = None,
    thread_root: Optional[str] = None,
) -> dict[str, Any]:
    """Build one client's report for one month. Raises :class:`NoAdAccount` to skip.

    ``feedback`` / ``previous`` / ``task_id`` / ``thread_root``: a revision asked for
    on the report's task (:func:`revise`), posted back in that thread."""
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

    analysis, recommendations = _analysis(summary, client, account, month, dry_run=dry_run,
                                          feedback=feedback, previous=previous)
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
        # Chromium renders the report (full-bleed, crisp) rather than the Drive
        # Docs path; upload is still Drive. Guarded because neither is dry-run aware.
        pdf_bytes = pdf_chromium.render(document)
        suffix = " - מתוקן" if feedback else ""
        saved = pdf.upload_pdf(pdf_bytes, f"דוח קמפיינים - {label}{suffix}.pdf", folder["id"])
        drive_url = saved.get("webViewLink") or drive_url

    # To Dror for approval: as a task on משימות, where his "שלח" sends it to the
    # client and a reply revises it; by email when there is no such list, or the
    # task could not be made (a revision asked on a task reports its failure there).
    delivered_to: Optional[str] = None
    if task_id or review_tasks.enabled():
        report = _Report(client, month, label, analysis, recommendations, pdf_bytes, drive_url)
        try:
            delivered_to = _deliver_as_task(report, task_id=task_id, thread_root=thread_root,
                                            dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            if task_id:
                raise
            auto.log_action("review_task_failed", "error", client_id=client_id, detail=str(exc))

    # Best-effort: the report is in Drive either way, and a mail failure must not
    # lose the work.
    dror_email = str(config.get("DROR_EMAIL") or "").strip()
    if dror_email and not delivered_to:
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

    if not feedback:  # a revision is announced in its own thread
        where = f"\nלאישור ושליחה ללקוח: {_task_url(delivered_to)}" if delivered_to else ""
        crm.append_automation_log(
            client_id, f"📊 דוח קמפיינים ל־{label} מוכן לאישור\n{drive_url}{where}")
    auto.log_action(
        "campaign_summary_revised" if feedback else "campaign_summary_ready",
        client_id=client_id, url=drive_url,
        detail=f"{label} · {campaign_report._money(totals.get('spend'), symbol)} · "
               f"{totals.get('leads', 0)} לידים",
    )
    return {"month": month, "summary": summary, "url": drive_url,
            "report": document, "task_id": delivered_to}


class _Report(NamedTuple):
    client: dict[str, Any]
    month: str
    label: str
    analysis: str
    recommendations: str
    pdf: bytes
    url: str


def _task_url(task_id: str) -> str:
    return f"https://app.clickup.com/t/{task_id}"


def _as_text(report: _Report) -> str:
    text = report.analysis
    if report.recommendations:
        text += f"\n\nהמלצות לחודש הבא:\n{report.recommendations}"
    return text


def _client_email(report: _Report) -> tuple[str, str]:
    """``(subject, body)`` of the email that takes the report to the client, in
    Dror's voice. A draft: Dror can change it in Gmail before his "שלח"."""
    name = report.client.get("first_name") or report.client.get("name") or ""
    subject = f"דוח הקמפיינים לחודש {report.label}"
    body = (f"היי {name},\n\n"
            f"מצורף דוח הקמפיינים לחודש {report.label}: מה קרה החודש, המספרים, "
            f"וההמלצות שלנו לחודש הבא.\n\n"
            f"אשמח לעבור עליו יחד. אם יש שאלות, אני כאן.")
    return subject, body


def _deliver_as_task(report: _Report, *, task_id: Optional[str], thread_root: Optional[str],
                     dry_run: bool) -> str:
    """Post the report as a version on its task in משימות (opened for the client and
    month if needed), with the PDF, and the client's email as a Gmail draft behind a
    📧 card. Returns the task id."""
    from ..lib import agent_tools
    from ..lib.clients.clickup import ClickUpClient
    from . import clickup_to_claude as bot

    clickup = ClickUpClient(dry_run=dry_run)
    kind = review_tasks.REPORT
    client_id = str(report.client["id"])
    if not task_id:
        name = f"{kind.prefix}{report.client.get('name') or client_id} · {report.label}"
        task_id = review_tasks.open_task(
            clickup, kind, client_id, name,
            f"דוח הקמפיינים החודשי ל{report.label}, מנתוני Meta Ads של הלקוח.")
    all_threads = bot.threads(clickup, task_id)
    version = bot.versions(all_threads) + 1
    text = (bot._comment_body(version, report.url, _as_text(report), "דוח קמפיינים")
            + f"\n{review_tasks.month_tag(report.month)}")
    root = review_tasks.post_version(clickup, task_id, text, thread_root)
    try:
        clickup.attach(task_id, report.pdf or b"%PDF-dry-run",
                       f"campaign-report-{report.month}-v{version}.pdf")
    except Exception:  # noqa: BLE001 - the PDF is also in Drive, linked above
        pass

    to = str(report.client.get("email") or "").strip()
    if not to:
        clickup.reply(root, "❌ אין מייל בכרטיס הלקוח, אז לא הוכנה טיוטה אליו. מוסיפים מייל "
                            "בכרטיס ועונים כאן קלוד כדי להכין אותה.")
    else:
        # A newer version replaces the draft waiting in this thread.
        thread = next((t for t in all_threads if str(t["root"]["id"]) == str(thread_root)), None)
        stale = bot.pending_card(thread) if thread else None
        if stale and not dry_run:
            agent_tools.delete_draft(stale["id"])
        subject, body = _client_email(report)
        attachments = [(f"דוח קמפיינים - {report.label}.pdf", report.pdf)] if report.pdf else None
        draft = agent_tools.create_draft(to, subject, body, attachments=attachments, dry_run=dry_run)
        clickup.reply(root, bot.card_text(draft))
    review_tasks.to_review(clickup, task_id)
    return task_id


def revise(task: dict[str, Any], thread: Optional[dict[str, Any]], feedback: str,
           all_threads: list[dict[str, Any]], *, dry_run: bool = False) -> dict[str, Any]:
    """Dror's reply on a report task: rebuild that month's report with his note and
    post it in the thread. No note (a bare "קלוד"): rebuild it as a new thread."""
    from . import clickup_to_claude as bot

    client_id = bot.linked_client_id(task)
    if not client_id:
        raise RuntimeError("המשימה לא מקושרת ללקוח בשדה לקוח")
    source = thread or (all_threads[-1] if all_threads else None)
    drafts = [c for c in ([source["root"], *source["replies"]] if source else []) if bot._is_draft(c)]
    month = next((m for c in reversed(drafts) if (m := review_tasks.month_in(bot._text(c)))), None)
    if not month:
        raise RuntimeError("לא מצאתי באיזה חודש הדוח הזה")
    previous = None
    if drafts:
        body = bot._text(drafts[-1]).split("\n\n", 1)[-1]
        previous = review_tasks._MONTH_TAG.sub("", body).strip()
    return run(client_id, month=month, dry_run=dry_run,
               feedback=feedback or None, previous=previous if feedback else None,
               task_id=str(task["id"]), thread_root=str(thread["root"]["id"]) if thread else None)


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

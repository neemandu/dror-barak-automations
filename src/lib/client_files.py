"""A client's documents as files in their own columns on the ClickUp task.

Dror works in the לקוחות list, so what the system made for a client sits there the
way the signed contract does (`חוזה חתום`): Attachment fields, one per kind, each
holding the PDFs in order (the latest last; a month's report, a strategy's
versions). The Doc or the Drive file stays the working copy; this is where Dror
sees it without looking for it.

Best-effort everywhere: a column the list does not have is skipped, and a failed
upload is logged, never allowed to fail the work that made the document.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from .logging_setup import get_logger

log = get_logger("client_files", "attach")

#: canonical field (crm_fields.ALIASES) -> the file name's stem. ClickUp refuses a
#: filename that is not ASCII.
COLUMNS = {
    "questionnaire_answers": "questionnaire-answers",
    "strategy_pdf": "strategy",
    "campaign_report_pdf": "campaign-report",
    "social_report_pdf": "social-report",
}


def file_name(field: str, tag: str = "") -> str:
    return f"{COLUMNS[field]}-{tag or date.today().isoformat()}.pdf"


def attach_pdf(crm: Any, client_id: str, field: str, pdf: bytes, *, tag: str = "",
               auto: Optional[Any] = None) -> bool:
    """Put ``pdf`` in the client's ``field`` column. True when it went in."""
    try:
        out = crm.attach_file(client_id, field, pdf, file_name(field, tag))
    except Exception as exc:  # noqa: BLE001
        log.warning("column_attach_failed", extra={"client_id": client_id, "field": field, "error": str(exc)})
        if auto is not None:
            auto.log_action("pdf_column_failed", "error", client_id=client_id, detail=f"{field}: {exc}")
        return False
    return "skipped" not in out


def attach_doc(crm: Any, client_id: str, field: str, doc_id: str, *, tag: str = "",
               auto: Optional[Any] = None, dry_run: bool = False) -> bool:
    """The Google Doc ``doc_id`` as a PDF in the client's ``field`` column."""
    if not doc_id:
        return False
    try:
        from . import task_docs

        pdf = b"%PDF-dry-run" if dry_run else task_docs.pdf_of(doc_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("doc_export_failed", extra={"client_id": client_id, "field": field, "error": str(exc)})
        if auto is not None:
            auto.log_action("pdf_column_failed", "error", client_id=client_id, detail=f"{field}: {exc}")
        return False
    return attach_pdf(crm, client_id, field, pdf, tag=tag, auto=auto)

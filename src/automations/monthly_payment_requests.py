"""T6 — Monthly billing.

Trigger: scheduled on the 1st of each month.

For every active client: issue a **חשבון עסקה** in Morning for their monthly price,
then email it to them, attached, in Dror's words.

Each client is handled independently — one client's failure must not stop the
rest of the month's billing.

**Order matters.** The document is created first and the email second, because a
client who receives "מצ״ב חשבון עסקה" with nothing attached has been sent a
mistake, whereas a document created but not emailed is simply a document Dror can
send by hand. The failure that costs least is the one to choose.

Manual/dry-run:
    python -m src.automations.monthly_payment_requests --dry-run
    python -m src.automations.monthly_payment_requests --client-id 86eya3gqt --dry-run
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from ..lib import emails
from ..lib.clients.crm import CrmClient
from ..lib.clients.morning import MorningClient
from .base import Automation, build_arg_parser, run_cli

NAME = "monthly_payment_requests"

_HEBREW_MONTHS = {
    1: "ינואר", 2: "פברואר", 3: "מרץ", 4: "אפריל", 5: "מאי", 6: "יוני",
    7: "יולי", 8: "אוגוסט", 9: "ספטמבר", 10: "אוקטובר", 11: "נובמבר", 12: "דצמבר",
}


def _month_label(month: str) -> str:
    """`2026-07` -> `יולי 2026`, because the client reads this, not a machine."""
    try:
        year, mon = month.split("-")
        return f"{_HEBREW_MONTHS[int(mon)]} {year}"
    except (ValueError, KeyError):
        return month


def _bill_one(
    client: dict[str, Any],
    *,
    morning: MorningClient,
    crm: CrmClient,
    auto: Automation,
    month: str,
    dry_run: bool,
) -> dict[str, Any]:
    client_id = str(client["id"])
    amount = client.get("monthly_price")
    if not amount:
        auto.log_action("no_price", "skipped", client_id=client_id,
                        detail="מחיר חודשי is not set on the ClickUp task")
        return {"skipped": "no price"}

    morning_client_id = client.get("morning_client_id")
    if not morning_client_id:
        # Onboarding creates the Morning client. Billing a client Morning has
        # never heard of would either fail or invent a second record for them.
        auto.log_action("no_morning_client", "skipped", client_id=client_id,
                        detail="no מזהה מורנינג on the task — has onboarding run?")
        return {"skipped": "no morning client"}

    label = _month_label(month)
    doc = morning.create_proforma(
        morning_client_id=str(morning_client_id),
        amount=float(amount),  # ex-VAT; Morning adds מע״מ
        description=f"ריטיינר חודשי — {label}",
        client_email=client.get("email") or None,
    )
    doc_id = str(doc.get("id") or "")
    auto.log_action("proforma_created", client_id=client_id,
                    detail=f"{amount}₪ + מע״מ · {label}", doc=doc_id,
                    url=_doc_url(doc))

    delivered = _email_it(client, doc, morning, label, auto, dry_run=dry_run)

    crm.update_fields(client_id, morning_status=f"חשבון עסקה {label}")
    crm.append_automation_log(
        client_id,
        f"💰 הופק חשבון עסקה — {label}\n"
        f"סכום: {amount}₪ + מע״מ\n"
        + (f"נשלח במייל ל־{client.get('email')}" if delivered
           else "לא נשלח במייל — יש לשלוח ידנית"),
    )
    return {"doc": doc_id, "emailed": delivered}


def _doc_url(doc: dict[str, Any]) -> str:
    url = doc.get("url")
    if isinstance(url, dict):
        return str(url.get("he") or url.get("origin") or "")
    return str(url or "")


def _email_it(
    client: dict[str, Any],
    doc: dict[str, Any],
    morning: MorningClient,
    label: str,
    auto: Automation,
    *,
    dry_run: bool,
) -> bool:
    """Email the חשבון עסקה, attached. Never raises: the document already exists."""
    to = str(client.get("email") or "").strip()
    client_id = str(client["id"])
    if not to:
        auto.log_action("no_email", "skipped", client_id=client_id,
                        detail="no אימייל on the task; send the document by hand")
        return False
    try:
        pdf = morning.download_document(str(doc.get("id") or ""))
        # "מצ״ב" promises an attachment. Sending the mail without one would be a
        # lie the client notices immediately.
        if not pdf:
            raise ValueError("Morning returned an empty document")
        emails.send_template(
            "monthly_proforma", to,
            client_name=client.get("first_name") or client.get("name") or "",
            month=label,
            attachments=[emails.Attachment(
                filename=f"proforma-{label.replace(' ', '-')}.pdf", content=pdf)],
            dry_run=dry_run,
        )
        auto.log_action("proforma_emailed", client_id=client_id, detail=to)
        return True
    except Exception as exc:  # noqa: BLE001
        auto.log_action("email_failed", "error", client_id=client_id,
                        detail=f"{exc} — the document exists; send it by hand")
        return False


def run(
    *, dry_run: bool = False, client_id: Optional[str] = None, month: Optional[str] = None
) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    morning = MorningClient(dry_run=dry_run)

    month = month or date.today().strftime("%Y-%m")
    clients = [crm.get_client(client_id)] if client_id else crm.list_active_clients()

    results = []
    for client in clients:
        try:
            results.append(_bill_one(client, morning=morning, crm=crm, auto=auto,
                                     month=month, dry_run=dry_run))
        except Exception as exc:  # keep billing the rest of the batch
            auto.log_action("billing_error", "error", client_id=str(client.get("id")),
                            detail=str(exc))
    auto.log_action("batch_done", detail=f"{len(results)}/{len(clients)} processed")
    return {"count": len(clients), "results": results}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", help="Bill a single client instead of all active")
    parser.add_argument("--month", help="Billing month, e.g. 2026-07 (default: current)")
    run_cli(parser, lambda a: run(dry_run=a.dry_run, client_id=a.client_id, month=a.month))


if __name__ == "__main__":
    main()

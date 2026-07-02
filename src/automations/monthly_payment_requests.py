"""T6 — Monthly payment requests.

Trigger: scheduled on the 1st of each month.
Action: pull all active clients from the CRM; for each, create a payment request
("דרישת תשלום") in Morning for their monthly price and send them a WhatsApp
message with the payment link. Each client is handled independently so one
failure doesn't stop the batch.

Manual/dry-run:
    python -m src.automations.monthly_payment_requests --dry-run
    python -m src.automations.monthly_payment_requests --client-id 42 --dry-run
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from ..lib import whatsapp_templates
from ..lib.clients.crm import CrmClient
from ..lib.clients.green_api import GreenApiClient
from ..lib.clients.morning import MorningClient
from .base import Automation, build_arg_parser, run_cli

NAME = "monthly_payment_requests"


def _bill_one(
    client: dict[str, Any],
    *,
    morning: MorningClient,
    wa: GreenApiClient,
    crm: CrmClient,
    auto: Automation,
    month: str,
) -> dict[str, Any]:
    client_id = str(client["id"])
    amount = client.get("monthly_price")
    if not amount:
        auto.log_action("no_price", "skipped", client_id=client_id, detail="no monthly_price")
        return {"skipped": True}

    doc = morning.create_payment_request(
        morning_client_id=client.get("morning_client_id") or "",
        amount=float(amount),
        description=f"ריטיינר חודשי — {month}",
    )
    pay_url = doc.get("url", {}).get("origin") if isinstance(doc.get("url"), dict) else doc.get("url")
    wa.send_message(
        client["phone"],
        whatsapp_templates.render(
            "payment_request",
            first_name=client.get("first_name", ""),
            month=month,
            payment_url=pay_url,
        ),
    )
    crm.update_fields(client_id, morning_status=f"billed:{month}")
    crm.append_automation_log(client_id, f"Payment request issued for {month}")
    auto.log_action("payment_requested", client_id=client_id, detail=f"{amount}₪ {month}", doc=doc.get("id"))
    return {"doc": doc}


def run(
    *, dry_run: bool = False, client_id: Optional[str] = None, month: Optional[str] = None
) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    morning = MorningClient(dry_run=dry_run)
    wa = GreenApiClient(dry_run=dry_run)

    month = month or date.today().strftime("%Y-%m")
    clients = [crm.get_client(client_id)] if client_id else crm.list_active_clients()

    results = []
    for client in clients:
        try:
            results.append(
                _bill_one(client, morning=morning, wa=wa, crm=crm, auto=auto, month=month)
            )
        except Exception as exc:  # keep billing the rest of the batch
            auto.log_action(
                "billing_error", "error", client_id=str(client.get("id")), detail=str(exc)
            )
    auto.log_action("batch_done", detail=f"{len(results)}/{len(clients)} processed")
    return {"count": len(clients), "results": results}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", help="Bill a single client instead of all active")
    parser.add_argument("--month", help="Billing month label, e.g. 2026-07 (default: current)")
    run_cli(parser, lambda a: run(dry_run=a.dry_run, client_id=a.client_id, month=a.month))


if __name__ == "__main__":
    main()

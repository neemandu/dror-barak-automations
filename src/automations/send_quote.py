"""T4 — Send quote + capture signature.

Two actions in one module:

  * ``send`` — trigger: manual ("send quote" button in the CRM, after the monthly
    price is set). Creates a Fillout quote link prefilled from CRM data and sends
    it to the client via WhatsApp; advances secondary status to ``quote_sent``.
  * ``signed`` — trigger: Fillout webhook when the client signs. Downloads the
    signed PDF, stores it in the client's Drive folder, writes the link back to the
    CRM, and advances secondary status to ``signed`` (which kicks off onboarding).

Manual/dry-run:
    python -m src.automations.send_quote --action send   --client-id 42 --dry-run
    python -m src.automations.send_quote --action signed --client-id 42 \\
        --submission-id sub_123 --dry-run
"""

from __future__ import annotations

from typing import Any, Optional

from ..lib import config, whatsapp_templates
from ..lib.clients.crm import SUB_QUOTE_SENT, SUB_SIGNED, CrmClient
from ..lib.clients.fillout import FilloutClient
from ..lib.clients.google import GoogleClient
from ..lib.clients.green_api import GreenApiClient
from .base import Automation, build_arg_parser, run_cli

NAME = "send_quote"


def send(client_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    fillout = FilloutClient(dry_run=dry_run)
    wa = GreenApiClient(dry_run=dry_run)

    client = crm.get_client(client_id)
    form_id = config.get("FILLOUT_QUOTE_FORM_ID", "quote-form")
    link = fillout.create_quote_link(
        form_id,
        prefill={
            "client_name": client.get("name"),
            "monthly_price": client.get("monthly_price"),
            "service_type": client.get("service_type"),
        },
    )
    wa.send_message(
        client["phone"],
        f"היי {client.get('first_name','')}, הצעת המחיר מוכנה לחתימה דיגיטלית:\n{link['url']}",
    )
    crm.update_fields(
        client_id, sub_status=SUB_QUOTE_SENT, quote_submission_id=link["submissionId"]
    )
    crm.append_automation_log(client_id, "Sent quote for digital signature")
    auto.log_action(
        "quote_sent",
        client_id=client_id,
        detail=link["url"],
        submission_id=link["submissionId"],
    )
    return {"link": link}


def signed(
    client_id: str, submission_id: str, *, dry_run: bool = False
) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    fillout = FilloutClient(dry_run=dry_run)
    google = GoogleClient(dry_run=dry_run)

    form_id = config.get("FILLOUT_QUOTE_FORM_ID", "quote-form")
    pdf = fillout.download_signed_pdf(form_id, submission_id)
    client = crm.get_client(client_id)
    folder_id = client.get("drive_folder_id") or config.get("DRIVE_DEFAULT_PARENT_ID")
    uploaded = google.upload_file(
        name=f"signed_quote_{client_id}.pdf",
        content=pdf,
        parent_id=folder_id or "root",
        mime_type="application/pdf",
    )
    crm.update_fields(
        client_id,
        sub_status=SUB_SIGNED,
        signed_contract_url=uploaded.get("webViewLink"),
    )
    crm.append_automation_log(
        client_id, "Signed quote stored in Drive; ready for onboarding"
    )
    auto.log_action(
        "quote_signed",
        client_id=client_id,
        detail=uploaded.get("webViewLink"),
        pdf_bytes=len(pdf),
    )
    return {"uploaded": uploaded}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--action", choices=["send", "signed"], required=True)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    parser.add_argument("--submission-id", help="Fillout submission id (for 'signed')")

    def handler(a: Any) -> Any:
        if a.action == "send":
            return send(a.client_id, dry_run=a.dry_run)
        if not a.submission_id:
            parser.error("--submission-id is required for --action signed")
        return signed(a.client_id, a.submission_id, dry_run=a.dry_run)

    run_cli(parser, handler)


if __name__ == "__main__":
    main()

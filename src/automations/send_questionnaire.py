"""T2 — Send questionnaire (WhatsApp).

Trigger: webhook when the CRM secondary status becomes ``initial_meeting`` (the
first meeting happened).
Action: send a WhatsApp (Green API) message with the questionnaire (Google Forms)
link, then advance the CRM secondary status to ``questionnaire_sent``.

Manual/dry-run:
    python -m src.automations.send_questionnaire --client-id 42 --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib import config, whatsapp_templates
from ..lib.clients.crm import SUB_QUESTIONNAIRE_SENT, CrmClient
from ..lib.clients.green_api import GreenApiClient
from .base import Automation, build_arg_parser, run_cli

NAME = "send_questionnaire"


def run(client_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    wa = GreenApiClient(dry_run=dry_run)

    client = crm.get_client(client_id)
    questionnaire_url = config.get(
        "QUESTIONNAIRE_URL", "https://forms.gle/your-questionnaire"
    )
    message = whatsapp_templates.render(
        "questionnaire",
        first_name=client.get("first_name", ""),
        questionnaire_url=questionnaire_url,
    )
    result = wa.send_message(client["phone"], message)
    crm.update_fields(client_id, sub_status=SUB_QUESTIONNAIRE_SENT)
    crm.append_automation_log(client_id, "Sent questionnaire link via WhatsApp")
    auto.log_action(
        "questionnaire_sent",
        client_id=client_id,
        detail=questionnaire_url,
        message_id=result.get("idMessage"),
    )
    return {"message": result}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="CRM client id")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

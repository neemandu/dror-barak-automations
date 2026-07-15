"""T4 — Send the quote/contract for signature.

Trigger: the `שלח הצעת מחיר` button on the ClickUp task.

Creates a signed, expiring link to our own signing page (:mod:`src.sign_page`) and
gets it to the client, then advances the secondary status to `נשלחה הצעת מחיר`.

This replaced Fillout. The `signed` half of the old module is gone with it: the
signing page finalises the contract itself — PDF into Drive, attachment onto the
ClickUp task, status to `חתם` — because it is the thing holding the signature at
the moment it is made. There is no webhook to wait for any more.

Delivery is deliberately best-effort: WhatsApp needs ManyChat and a Meta-approved
template, and until that exists the link is posted as a comment on the task so
Dror can send it himself. A quote that cannot be auto-delivered is not a failed
quote — refusing to produce the link would make the button useless.

Manual/dry-run:
    python -m src.automations.send_quote --client-id 86eya3gqt --dry-run
"""

from __future__ import annotations

from typing import Any

from ..lib import contract, signing
from ..lib.clients.crm import SUB_QUOTE_SENT, CrmClient
from ..lib.logging_setup import get_logger
from .base import Automation, build_arg_parser, run_cli

NAME = "send_quote"
_log = get_logger(NAME, "deliver")


def send(client_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    client = crm.get_client(client_id)

    # Check the contract can actually be produced before sending anyone a link.
    # A client who opens it and finds "סך של  ₪" has been made to look at a
    # broken document, and the price is Dror's to know, not theirs to fill in.
    fields = contract.fields_from_client(client)
    if not str(fields.get("price_strategy") or "").strip("0, "):
        auto.log_action("no_price", "error", client_id=client_id,
                        detail="מחיר חודשי is not set on the ClickUp task")
        raise ValueError(
            f"client {client_id} has no monthly price on the ClickUp task; "
            f"set מחיר חודשי before sending a quote"
        )

    url = signing.sign_url(client_id)

    # The details ClickUp has no fields for are collected on the signing page —
    # the client knows their own ח.פ. Only note it here so it is not a surprise.
    asked_on_page = contract.missing_for(fields)

    delivered = _deliver(crm, client, url, dry_run=dry_run)
    crm.update_fields(client_id, sub_status=SUB_QUOTE_SENT)
    crm.append_automation_log(
        client_id,
        "📄 הצעת מחיר נשלחה לחתימה\n"
        f"קישור לחתימה: {url}\n"
        + (f"נשלח ב־{delivered}" if delivered else
           "לא נשלח אוטומטית — יש לשלוח את הקישור ללקוח ידנית"),
    )
    auto.log_action("quote_sent", client_id=client_id, url=url,
                    detail=f"delivered via {delivered}" if delivered
                    else "link posted to the task; send it manually",
                    asks_client_for=asked_on_page)
    return {"url": url, "delivered": delivered, "asks_client_for": asked_on_page}


def _deliver(crm: CrmClient, client: dict[str, Any], url: str, *, dry_run: bool) -> str:
    """Try to get the link to the client. Returns how, or "" if we could not.

    Never raises: the link is on the task either way, and a delivery failure must
    not lose it. Email first — it carries Dror's own wording and needs no Meta
    approval; WhatsApp is added when ManyChat exists.
    """
    from ..lib import emails

    to = str(client.get("email") or "").strip()
    if not to:
        _log.warning("no_client_email", extra={"client_id": client.get("id")})
        return ""
    try:
        emails.send_template(
            "sign_contract", to,
            client_name=client.get("first_name") or client.get("name") or "",
            cta_url=url,
            dry_run=dry_run,
        )
        return "אימייל"
    except Exception as exc:  # noqa: BLE001
        _log.warning("email_failed", extra={"to": to, "error": str(exc)})
        return ""


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="ClickUp task id")
    run_cli(parser, lambda a: send(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

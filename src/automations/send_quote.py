"""T4 — Send the quote/contract for signature.

Trigger: the `שלח חוזה ללקוח במייל` button on the ClickUp task.

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

from typing import Any, Optional

from ..lib import contract, contract_store, idempotency, signing
from ..lib.clients.crm import SUB_IN_WORK, SUB_QUOTE_SENT, SUB_SIGNED, CrmClient
from ..lib.logging_setup import get_logger
from .base import Automation, build_arg_parser, run_cli

NAME = "send_quote"
_log = get_logger(NAME, "deliver")


def send(client_id: str, *, dry_run: bool = False, email: bool = True,
         source: str = "מ-ClickUp", new_contract: bool = False) -> dict[str, Any]:
    """Send a client their contract to sign.

    The ClickUp button and the dashboard's send button both land here, so a quote
    is the same thing whichever sent it. ``email=False`` makes the link without
    emailing it (the dashboard's "copy the link", for Dror to send by WhatsApp);
    everything else (status, reminders, the log) is the same, because the client
    is being offered the contract either way.

    A client who already signed is refused unless ``new_contract`` says a new one
    is meant: sending marks the signature superseded, so a stray press on the
    button would quietly undo it. Only the dashboard's contract page passes it,
    after a confirmation that says the client signed.
    """
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    client = crm.get_client(client_id)

    if not new_contract:
        _refuse_if_signed(auto, client_id, client, dry_run=dry_run)

    # Check the contract can actually be produced before sending anyone a link.
    # A client who opens it and finds "סך של  ₪" has been made to look at a
    # broken document, and the price is Dror's to know, not theirs to fill in.
    fields = contract.fields_from_client(client)
    if contract.prices(client)["total"] <= 0:
        from ..lib.actions import Refused

        auto.log_action("no_price", "error", client_id=client_id,
                        detail="אין מחיר במשימה ב-ClickUp")
        raise Refused("אין מחיר במשימה. ממלאים את המחיר החודשי ולוחצים שוב.")

    # Dror's own version for this client, if he edited it on the dashboard. It must
    # render before anyone gets a link: a reference to a clause he deleted is a
    # refusal with its reason, not a broken page on the client's phone.
    version = contract_store.draft(client_id)
    if version:
        from ..lib.actions import Refused

        display = {**fields, **{k: f or "-" for k, f in fields.items() if k in contract.REQUIRED_CLIENT_FIELDS}}
        try:
            contract.render(display, template=version["template"],
                            signatures={"provider_signature": "", "client_signature": ""})
        except contract.ContractError as exc:
            auto.log_action("contract_version_broken", "error", client_id=client_id, detail=str(exc))
            raise Refused(f"הנוסח שערכת לחוזה הזה לא תקין: {exc}") from exc

    url = signing.sign_url(client_id)

    # The details ClickUp has no fields for are collected on the signing page —
    # the client knows their own ח.פ. Only note it here so it is not a surprise.
    asked_on_page = contract.missing_for(fields)

    delivered = _deliver(crm, client, url, dry_run=dry_run) if email else ""
    reminder_task = ""
    # Start the reminder clock, and open the follow-up as a task Dror can see and
    # edit (reminder_tasks): sent automatically on its due date if they have not
    # signed. Signing clears the record and closes the task.
    if not dry_run:
        # The version that goes out, frozen: editing it later does not change what
        # this client signs.
        contract_store.freeze_sent(client_id)
        signing.mark_pending(client_id)
        try:
            from ..lib import reminder_tasks

            task_id = reminder_tasks.open_task(client_id, {**client, "id": client_id})
            if task_id:
                signing.set_reminder_task(client_id, task_id)
                reminder_task = task_id
        except Exception as exc:  # noqa: BLE001 - the quote is sent; the fallback template still reminds
            auto.log_action("reminder_task_failed", "error", client_id=client_id, detail=str(exc))
        # A new quote is a new contract: an earlier signature no longer answers for
        # the link, and the one-signature guard opens for this one.
        contract_store.supersede(client_id)
        idempotency.release(idempotency.guard("signed_contract", client_id))
    import time

    # The date in its own column (when the list has one): a second press is not a surprise.
    crm.update_fields(client_id, sub_status=SUB_QUOTE_SENT, quote_sent_at=int(time.time() * 1000))
    how = (f"נשלח ב{delivered}" if delivered else
           "הקישור נוצר בדשבורד, והשליחה ללקוח ידנית" if not email else
           "לא נשלח אוטומטית, יש לשלוח את הקישור ללקוח ידנית")
    crm.append_automation_log(
        client_id, _message(client, url, delivered=bool(delivered), email=email,
                            price=contract.prices(client)["total"], reminder=bool(reminder_task),
                            version=version))
    auto.log_action("quote_sent", client_id=client_id, url=url,
                    detail=f"{how} ({source})" + (f", נוסח מותאם: {version['summary']}" if version else ""),
                    asks_client_for=asked_on_page, source=source)
    return {"url": url, "delivered": delivered, "to": str(client.get("email") or "") if delivered else "",
            "asks_client_for": asked_on_page}


def _refuse_if_signed(auto: Automation, client_id: str, client: dict[str, Any], *,
                      dry_run: bool) -> None:
    """Refuse a new contract for a client who signed: on our page (a current
    signature) or otherwise (the task is already at `חתם` or `בעבודה`, e.g. after
    a paper signature)."""
    from ..lib.actions import Refused

    on_page = None if dry_run else contract_store.current_signature(client_id)
    if not on_page and client.get("sub_status") not in (SUB_SIGNED, SUB_IN_WORK):
        return
    name = str(client.get("name") or client_id)
    when = f" ב-{signing.local_time(on_page['audit']['signed_at'])}" if on_page else ""
    auto.log_action("quote_refused_signed", "skipped", client_id=client_id,
                    detail=f"{name} כבר חתם{when}")
    raise Refused(
        f"ההסכם עם {name} כבר נחתם{when}, אז לא שלחתי חוזה חדש: שליחה הייתה מבטלת את "
        "החתימה הקיימת. אם צריך חוזה חדש (מחיר או שירות שהשתנו), שולחים מדף החוזה "
        "בדשבורד, 'שליחת חוזה חדש'.")


def _message(client: dict[str, Any], url: str, *, delivered: bool, email: bool, price: float,
             reminder: bool, version: Optional[dict[str, Any]] = None) -> str:
    """What the press (or the dashboard's send) did, as the one comment on the task.

    Says who it went to and for how much, and what happens next, because the button
    is Dror's only moment to catch a wrong price or address. Never claims a send
    that did not happen.
    """
    from ..lib import reminder_tasks

    to = str(client.get("email") or "").strip()
    cost = f"{contract._shekels(price)} ₪"
    link = f"קישור לחתימה: {url}"
    # Which version went out: a button press in ClickUp does not show it otherwise.
    which = (f"זה הנוסח שערכת ללקוח הזה ({version['summary']})." if version and version.get("summary") else
             "זה הנוסח שערכת ללקוח הזה." if version else "זה הנוסח הרגיל של החוזה.")
    if delivered:
        lines = [f"היי, שלחתי את ההסכם לחתימה אל {to}, על סך {cost}. {which}", link]
        if reminder:
            lines.append(f"אם לא ייחתם, תישלח תזכורת אחת בעוד {reminder_tasks.DAYS} ימים, מהמשימה "
                         f"'{reminder_tasks.PREFIX}…' ב'משימות'. אפשר לערוך אותה או לסגור כדי לבטל.")
        return "\n".join(lines)
    if not email:
        return "\n".join([f"היי, יצרתי את קישור החתימה (על סך {cost}) ולא שלחתי אותו, כמו שביקשת. {which}",
                          link, "את השליחה ללקוח אפשר לעשות ידנית."])
    why = "אין מייל במשימה" if not to else f"השליחה אל {to} נכשלה"
    return "\n".join([f"היי, יצרתי את קישור החתימה אבל לא שלחתי אותו ללקוח: {why}. {which}", link,
                      "אפשר להוסיף מייל וללחוץ שוב, או לשלוח את הקישור ידנית."])


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

"""Smoove → ManyChat — turn a Smoove webhook into a WhatsApp Flow.

Trigger: a POST from Smoove (a marketing automation platform) carrying a lead:

    {"f_name": "דנה", "cellphone": "0501234567", "msg": "ai_agents"}

Action: make sure the person is a ManyChat contact (find, or create with the
recorded opt-in phrase), then trigger the ManyChat **Flow** named by ``msg``.

Why a Flow and not a text message: every message here is business-initiated and so
outside WhatsApp's 24-hour window, where the official Meta API delivers **only
approved templates** — which ManyChat sends as Flows. See CLAUDE.md.

``msg`` → Flow is resolved from config, so Dror adds a new message type by setting
one env var, no code change: ``msg="ai_agents"`` looks up ``MANYCHAT_FLOW_AI_AGENTS``.
A ``msg`` with no mapping is **rejected**, not guessed — the endpoint is public, and
firing an arbitrary Flow (each one billed) on request is not something an unmapped
value should be able to do.

Manual/dry-run:
    python -m src.automations.smoove_to_manychat \
        --first-name דנה --phone 0501234567 --msg ai_agents --dry-run
"""

from __future__ import annotations

import re
from typing import Any

from ..lib import config
from ..lib.clients.manychat import ManyChatClient, to_e164
from .base import Automation, build_arg_parser, run_cli

NAME = "smoove_to_manychat"


def flow_env_key(msg: str) -> str:
    """The env var a ``msg`` maps to, e.g. ``ai_agents`` -> ``MANYCHAT_FLOW_AI_AGENTS``."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", msg.strip()).strip("_").upper()
    return f"MANYCHAT_FLOW_{slug}"


def flow_for(msg: str) -> str | None:
    """The configured Flow id for ``msg``, or ``None`` if none is mapped."""
    if not msg or not msg.strip():
        return None
    return config.get(flow_env_key(msg))


def run(
    first_name: str, cellphone: str, msg: str, *, dry_run: bool = False
) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)

    phone = to_e164(cellphone, config.get("SMOOVE_DEFAULT_COUNTRY_CODE", "972"))
    if not phone:
        auto.log_action(
            "no_phone", "skipped",
            detail=f"Smoove payload had no usable phone (cellphone={cellphone!r})",
        )
        return {"skipped": "no phone"}

    flow_ns = flow_for(msg)
    if not flow_ns:
        # A public endpoint: an unmapped msg must not fall through to a default
        # Flow. Name the missing key so the fix is one line in .env.
        auto.log_action(
            "unknown_msg", "skipped", client_id=phone,
            detail=f"no Flow mapped for msg={msg!r}; set {flow_env_key(msg or '')} in .env",
            msg=msg,
        )
        return {"skipped": f"no flow for msg={msg!r}"}

    mc = ManyChatClient(dry_run=dry_run)
    subscriber_id, created = mc.ensure_subscriber(phone, first_name or "")
    mc.send_flow(subscriber_id, flow_ns)

    auto.log_action(
        "flow_sent",
        client_id=phone,
        detail=(f"{'נוצר איש קשר חדש' if created else 'איש קשר קיים'} — "
                f"נשלח Flow '{msg}' ל־{phone}"),
        msg=msg,
        subscriber_id=subscriber_id,
        created=created,
    )
    return {
        "phone": phone,
        "subscriber_id": subscriber_id,
        "created": created,
        "msg": msg,
        "flow_ns": flow_ns,
    }


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--first-name", default="", help="Lead's first name")
    parser.add_argument("--phone", required=True, help="Lead's cellphone")
    parser.add_argument("--msg", required=True, help="Message key, e.g. ai_agents")
    run_cli(
        parser,
        lambda a: run(a.first_name, a.phone, a.msg, dry_run=a.dry_run),
    )


if __name__ == "__main__":
    main()

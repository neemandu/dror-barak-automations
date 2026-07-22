"""AWS Lambda entrypoint — the Smoove webhook (a standalone function).

Smoove POSTs a lead here when one of Dror's automations fires:

    {"f_name": "דנה", "cellphone": "0501234567", "msg": "ai_agents"}

This function parses that, then hands off to :mod:`src.automations.smoove_to_manychat`,
which finds/creates the ManyChat contact and triggers the Flow named by ``msg``.

It is deliberately its own Lambda + its own API Gateway, separate from the ClickUp
webhook — a different source, a different payload, a different blast radius.

**Auth is optional and off by default.** The endpoint is public: anyone who finds
the URL can create ManyChat contacts and fire billed WhatsApp Flows. Set
``SMOOVE_WEBHOOK_TOKEN`` (and have Smoove send it as the ``X-Smoove-Token`` header,
or ``?token=`` in the URL) to require a shared secret. When the var is empty the
endpoint is open — fine for a first test, but turn it on before real traffic.

**Idempotency.** Smoove may retry a delivery. A short-lived claim keyed on
``phone+msg`` stops a retry from creating a second contact / sending a second
(billed) message. Best-effort: it needs ``IDEMPOTENCY_TABLE`` to work across Lambda
instances; without it the claim lives only within one warm instance.

Env: ``MANYCHAT_API_KEY``, ``MANYCHAT_CONSENT_PHRASE``, ``MANYCHAT_FLOW_<MSG>`` (one
per message type), optional ``SMOOVE_WEBHOOK_TOKEN``, ``IDEMPOTENCY_TABLE``,
``RUN_LOG_TABLE``.
"""

from __future__ import annotations

import base64
import hmac
import json
from typing import Any
from urllib.parse import parse_qs

from .automations import smoove_to_manychat
from .lib import config, idempotency
from .lib.clients.manychat import to_e164
from .lib.logging_setup import get_logger

log = get_logger("webhook", "smoove")

# Retries arrive within seconds/minutes; a genuine re-send of the same person for
# the same message weeks later is rare and harmless to re-run. Keep the claim
# short so it dedupes retries without blocking a legitimate later send.
CLAIM_TTL_SECONDS = 6 * 60 * 60


class Rejected(Exception):
    """Request refused before any work was done."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def verify_token(supplied: str) -> None:
    """Authenticate the caller — only when a token is configured.

    No token set = open endpoint (the documented "no auth for now" default). Once
    ``SMOOVE_WEBHOOK_TOKEN`` is set it is required and constant-time compared, so
    turning auth on is a one-variable change with no code deploy.
    """
    expected = config.get("SMOOVE_WEBHOOK_TOKEN")
    if not expected:
        return
    if not supplied:
        raise Rejected(401, "missing Smoove webhook token")
    if not hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
        raise Rejected(401, "bad Smoove webhook token")


def parse_body(raw: str) -> dict[str, Any]:
    """Read the lead out of the body, whether Smoove sends JSON or a form post."""
    raw = raw or ""
    stripped = raw.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise Rejected(400, f"body is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise Rejected(400, "JSON body is not an object")
        return data
    # Form-encoded fallback: parse_qs gives lists; take the first of each.
    return {k: v[0] for k, v in parse_qs(raw).items() if v}


def handle(raw: str, token: str, dry_run: bool = False) -> dict[str, Any]:
    """Verify, parse, dedupe, and dispatch. Returns the response body."""
    verify_token(token)
    payload = parse_body(raw)

    first_name = str(payload.get("f_name") or "").strip()
    cellphone = str(payload.get("cellphone") or "").strip()
    msg = str(payload.get("msg") or "").strip()
    if not cellphone or not msg:
        raise Rejected(400, "payload must include cellphone and msg")

    # Key on the normalised phone so 050-1234567 and +9725012... dedupe together.
    phone = to_e164(cellphone, config.get("SMOOVE_DEFAULT_COUNTRY_CODE", "972"))
    key = f"smoove:{phone}:{msg}"
    if not idempotency.claim(key, ttl=CLAIM_TTL_SECONDS):
        log.info("duplicate_ignored", extra={"key": key})
        return {"ok": True, "duplicate": True}

    try:
        result = smoove_to_manychat.run(first_name, cellphone, msg, dry_run=dry_run)
    except Exception:
        # Give the claim back so a Smoove retry can complete the work.
        idempotency.release(key)
        raise
    idempotency.complete(key)
    return {"ok": True, "result": result}


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """API Gateway HTTP API (payload v2) entrypoint."""
    config.load_dotenv()

    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    params = event.get("queryStringParameters") or {}
    token = headers.get("x-smoove-token", "") or str(params.get("token") or "")

    dry_run = config.get_bool("WEBHOOK_DRY_RUN")

    try:
        body = handle(raw, token, dry_run=dry_run)
        return _response(200, {**body, "dry_run": dry_run} if dry_run else body)
    except Rejected as exc:
        log.warning("rejected", extra={"status": exc.status, "reason": exc.reason})
        return _response(exc.status, {"ok": False, "error": exc.reason})
    except Exception as exc:  # noqa: BLE001
        # 500 so Smoove retries; any idempotency claim has already been released.
        log.error("smoove_failed", extra={"error": str(exc)})
        return _response(500, {"ok": False, "error": str(exc)})


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False),
    }

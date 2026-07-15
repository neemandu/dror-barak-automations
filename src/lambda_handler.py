"""AWS Lambda entrypoint — ClickUp webhooks behind API Gateway.

API Gateway (HTTP API, payload format 2.0) invokes this. It does four things, in
this order, and the order matters:

1. **Verify the signature.** The endpoint is public; anything on the internet can
   POST to it. An unverified request could trigger onboarding — or a payment —
   for any client id. Unsigned traffic is rejected before it is even parsed.
2. **Claim the delivery.** ClickUp retries what it thinks failed, so the same
   event arrives more than once. See :mod:`src.lib.idempotency`.
3. **Dispatch** to the automation.
4. **Release the claim if the work failed**, so ClickUp's retry can succeed.

Reused by `sam local start-api` and the tests, so the routing logic is one
function (:func:`handle`) that knows nothing about AWS.

Env: ``CLICKUP_WEBHOOK_SECRET`` (from the webhook registration),
``IDEMPOTENCY_TABLE``, plus whatever the dispatched automations need.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from .lib import actions, config, idempotency
from .lib.clients.crm import SUB_INITIAL_MEETING, SUB_SIGNED
from .lib.crm_fields import canonical_sub_status
from .lib.logging_setup import get_logger

log = get_logger("webhook", "lambda")


class Rejected(Exception):
    """Request refused before any work was done."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def verify_signature(raw_body: str, signature: str) -> None:
    """Reject anything not signed with the webhook secret.

    ClickUp signs the raw body with HMAC-SHA256, hex-encoded, in ``X-Signature``.
    The body must be hashed exactly as received — re-serialising the parsed JSON
    changes the bytes and the signature will never match.
    """
    secret = config.get("CLICKUP_WEBHOOK_SECRET")
    if not secret:
        # Fail closed. Accepting unsigned traffic because a secret is missing is
        # how a public endpoint becomes an open trigger for onboarding.
        raise Rejected(500, "CLICKUP_WEBHOOK_SECRET is not set; refusing to serve")
    if not signature:
        raise Rejected(401, "missing X-Signature")
    expected = hmac.new(
        secret.encode("utf-8"), raw_body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise Rejected(401, "bad signature")


def _sub_status_of(task_id: str, dry_run: bool) -> str | None:
    """The client's secondary status *right now*, read back from ClickUp.

    Deliberately not parsed out of the webhook payload. For a custom-field change
    ClickUp puts an option index or id in ``history_items[].after`` rather than a
    label, and the encoding varies by field type — code that guessed it would pass
    its tests against an invented payload and then silently never fire.

    Reading current state costs one API call and is correct regardless of payload
    shape. It also self-corrects: if two changes arrive out of order, both agree on
    where the client actually is.
    """
    from .lib.clients.crm import CrmClient

    client = CrmClient(dry_run=dry_run).get_client(task_id)
    sub = client.get("sub_status")
    return canonical_sub_status(str(sub)) or (str(sub) if sub else None)


def route(payload: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    """Pick the automation for a ClickUp event and run it."""
    from .automations import (
        clickup_to_claude,
        lead_to_contacts,
        onboarding,
        send_questionnaire,
    )

    event = str(payload.get("event") or "")
    task_id = str(payload.get("task_id") or "")
    if not task_id:
        raise Rejected(400, "payload has no task_id")

    tasks_list = config.get("CLICKUP_TASKS_LIST_ID")
    # A משימות task is work for Claude Code, not a client. Same webhook shape, so
    # the list id is what tells them apart.
    if tasks_list and str(payload.get("list_id") or "") == tasks_list:
        return clickup_to_claude.run(task_id, dry_run=dry_run)

    if event == "taskCreated":
        return lead_to_contacts.run(task_id, dry_run=dry_run)

    if event in ("taskUpdated", "taskStatusUpdated"):
        sub = _sub_status_of(task_id, dry_run)
        if sub == SUB_INITIAL_MEETING:
            return send_questionnaire.run(task_id, dry_run=dry_run)
        if sub == SUB_SIGNED:
            # Layer 2: distinct events can mean the same thing. Moving a client
            # חתם -> בעבודה -> חתם is two real events, and layer 1 lets both
            # through. Onboarding creates a Drive folder and a Morning client, so
            # running it twice is not recoverable by retrying.
            once = idempotency.guard("onboarding", task_id)
            if not idempotency.claim(once):
                log.info("onboarding_already_done", extra={"client_id": task_id})
                return {"skipped": "onboarding already ran for this client"}
            try:
                return onboarding.run(task_id, dry_run=dry_run)
            except Exception:
                idempotency.release(once)  # let a retry finish the job
                raise
        return {"ignored": f"no automation for {event} -> {sub}"}

    return {"ignored": f"no automation for event {event}"}


def verify_automation_token(supplied: str) -> None:
    """Authenticate a button press.

    ClickUp's Automation webhooks are configured in the UI and are **not** signed
    the way API-registered webhooks are — the only thing distinguishing a genuine
    press from anyone on the internet is a header we ask ClickUp to send. Without
    it this endpoint would send quotes to Dror's clients on request.
    """
    expected = config.get("AUTOMATION_TOKEN")
    if not expected:
        raise Rejected(500, "AUTOMATION_TOKEN is not set; refusing to serve")
    if not supplied:
        raise Rejected(401, "missing X-Automation-Token")
    if not hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
        raise Rejected(401, "bad automation token")


def handle_action(
    action_key: str, raw_body: str, token: str, dry_run: bool = False
) -> dict[str, Any]:
    """Run the automation behind a ClickUp button press."""
    verify_automation_token(token)

    action = actions.get(action_key)
    if not action:
        raise Rejected(400, f"unknown action {action_key!r}; "
                            f"expected one of {sorted(actions.ACTIONS)}")
    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError as exc:
        raise Rejected(400, f"body is not JSON: {exc}") from exc

    if _is_clickup_test_ping(payload):
        # ClickUp's Automation editor has a Test button that posts
        # {"body": "Test message from ClickUp Webhooks Service"} — no task, because
        # it cannot know which task you would press the real button on. Answering
        # 400 is defensible but reads as "your button is broken" at the exact
        # moment someone is checking whether their button works.
        log.info("clickup_test_ping", extra={"action": action_key})
        return {
            "ok": True,
            "test": True,
            "action": action.key,
            "message": "Webhook reachable and authenticated. This was ClickUp's "
                       "Test button, which sends no task — press the real button "
                       "on a client task to run the automation.",
        }

    task_id = actions.task_id_of(payload)
    if not task_id:
        # The body is the only useful evidence for this failure, and without it a
        # misconfigured payload is indistinguishable from a test ping.
        log.warning(
            "action_payload_without_task",
            extra={
                "action": action_key,
                "keys": sorted(payload.keys())[:20],
                "body": raw_body[:800],
            },
        )
        raise Rejected(400, "payload has no task id")

    key = actions.click_key(action.key, task_id, payload)
    if not idempotency.claim(key):
        log.info("duplicate_click_ignored", extra={"key": key})
        return {"ok": True, "duplicate": True, "action": action.key}

    if action.once_only:
        once = idempotency.guard(action.key, task_id)
        if not idempotency.claim(once):
            return {"ok": True, "skipped": f"{action.key} already ran for this client"}

    try:
        result = action.run(task_id, dry_run)
    except Exception as exc:  # noqa: BLE001
        idempotency.release(key)
        # Dror pressed a button and is waiting. Silence would leave him wondering
        # whether the quote went out; say so where he pressed it.
        _comment(task_id, f"❌ {action.label} נכשל: {exc}", dry_run)
        raise

    idempotency.complete(key)
    _comment(task_id, action.confirm, dry_run)
    return {"ok": True, "action": action.key, "result": result}


def _is_clickup_test_ping(payload: dict[str, Any]) -> bool:
    """ClickUp's "Test" button in the Automation editor, not a real press.

    Matched on shape rather than the exact sentence: a lone ``body`` string and
    nothing else is never a real automation payload, which always carries a task.
    """
    if set(payload.keys()) != {"body"}:
        return False
    return isinstance(payload.get("body"), str)


def _comment(task_id: str, message: str, dry_run: bool) -> None:
    """Report back on the task. Never let feedback break the actual work.

    Posted for real **even in dry-run**, and labelled as such. A comment on Dror's
    own task is feedback to him, not an outward side effect: dry-run exists to
    keep us from creating Drive folders and messaging clients, not to hide from
    Dror whether his button worked. Mocking it left a press with no visible
    result, which is indistinguishable from a broken button.
    """
    if dry_run:
        message = f"🧪 הרצת ניסיון (לא בוצע בפועל): {message}"
    try:
        from .lib.clients.crm import CrmClient

        CrmClient(dry_run=False).append_automation_log(task_id, message)
    except Exception as exc:  # noqa: BLE001
        log.warning("comment_failed", extra={"task_id": task_id, "error": str(exc)})


def handle(raw_body: str, signature: str, dry_run: bool = False) -> dict[str, Any]:
    """Verify, dedupe, dispatch. Returns the response body."""
    verify_signature(raw_body, signature)

    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError as exc:
        raise Rejected(400, f"body is not JSON: {exc}") from exc

    key = idempotency.event_key(payload, raw_body)
    if not idempotency.claim(key):
        # Not an error: ClickUp resending is normal. Say so plainly and 200, or it
        # will keep retrying a delivery we have already acted on.
        log.info("duplicate_ignored", extra={"key": key, "event": payload.get("event")})
        return {"ok": True, "duplicate": True, "key": key}

    try:
        result = route(payload, dry_run=dry_run)
    except Exception:
        # Give the claim back so ClickUp's retry can do the work. Holding it would
        # turn one blip into a permanently skipped onboarding.
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
    signature = headers.get("x-signature", "")

    # Lets a real deployed stack take real ClickUp events and prove the wiring —
    # signature, dedup, routing — while the automations touch nothing. Worth
    # having for the first deploy, since the alternative is finding out by
    # creating a live Morning client.
    dry_run = config.get_bool("WEBHOOK_DRY_RUN")

    path = str((event.get("requestContext") or {}).get("http", {}).get("path")
               or event.get("rawPath") or "")
    params = event.get("queryStringParameters") or {}

    try:
        if path.endswith("/action"):
            # A button press: an Automation webhook, authenticated by header
            # rather than signature, with the action named in the query string.
            body = handle_action(
                str(params.get("action") or ""),
                raw,
                headers.get("x-automation-token", ""),
                dry_run=dry_run,
            )
        else:
            body = handle(raw, signature, dry_run=dry_run)
        return _response(200, {**body, "dry_run": dry_run} if dry_run else body)
    except Rejected as exc:
        log.warning("rejected", extra={"status": exc.status, "reason": exc.reason})
        return _response(exc.status, {"ok": False, "error": exc.reason})
    except Exception as exc:  # noqa: BLE001
        # 500 so ClickUp retries; the claim has already been released.
        log.error("webhook_failed", extra={"error": str(exc)})
        return _response(500, {"ok": False, "error": str(exc)})


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False),
    }

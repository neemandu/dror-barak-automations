"""Smoove → ManyChat: phone normalisation, the automation, and the Lambda.

The endpoint is public and every Flow it fires is billed, so the paths that must
not misbehave — an unmapped msg, a duplicate delivery, a missing token when one is
required — matter as much as the happy path.
"""

from __future__ import annotations

import json

import pytest

from src import smoove_handler
from src.automations import smoove_to_manychat
from src.lib.http import HttpError
from src.lib.clients.manychat import ManyChatClient, to_e164


@pytest.fixture(autouse=True)
def _idem_file(tmp_path, monkeypatch):
    # File-backed idempotency, isolated per test (conftest already drops the table).
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "idem.json"))
    monkeypatch.delenv("SMOOVE_WEBHOOK_TOKEN", raising=False)
    yield


def _actions(read_log):
    return {e["action"] for e in read_log()}


# ------------------------------------------------------------- phone normalising


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0501234567", "+972501234567"),      # local trunk zero
        ("050-123-4567", "+972501234567"),    # punctuation stripped
        ("+972501234567", "+972501234567"),   # already E.164
        ("00972501234567", "+972501234567"),  # international 00 prefix
        ("972501234567", "+972501234567"),    # bare country code
        ("", ""),                              # nothing usable
    ],
)
def test_to_e164(raw, expected):
    assert to_e164(raw) == expected


# ---------------------------------------------------------------- the automation


def test_creates_contact_and_sends_flow(read_log, monkeypatch):
    monkeypatch.setenv("MANYCHAT_FLOW_AI_AGENTS", "content_ai_agents")
    result = smoove_to_manychat.run("דנה", "0501234567", "ai_agents", dry_run=True)
    assert result["phone"] == "+972501234567"
    assert result["flow_ns"] == "content_ai_agents"
    assert result["created"] is True
    assert "flow_sent" in _actions(read_log)


def test_existing_contact_is_not_recreated(read_log, monkeypatch):
    monkeypatch.setenv("MANYCHAT_FLOW_AI_AGENTS", "content_ai_agents")
    monkeypatch.setattr(
        ManyChatClient, "find_subscriber", lambda self, phone: "existing-42"
    )
    result = smoove_to_manychat.run("דנה", "0501234567", "ai_agents", dry_run=True)
    assert result["subscriber_id"] == "existing-42"
    assert result["created"] is False


def test_unmapped_msg_is_rejected_not_guessed(read_log):
    # No MANYCHAT_FLOW_* set: an unknown msg must not fall through to a default.
    result = smoove_to_manychat.run("דנה", "0501234567", "ai_agents", dry_run=True)
    assert "skipped" in result
    assert "unknown_msg" in _actions(read_log)


def test_missing_phone_is_skipped(read_log, monkeypatch):
    monkeypatch.setenv("MANYCHAT_FLOW_AI_AGENTS", "content_ai_agents")
    result = smoove_to_manychat.run("דנה", "", "ai_agents", dry_run=True)
    assert "skipped" in result
    assert "no_phone" in _actions(read_log)


def test_flow_env_key_slugifies_the_msg():
    assert smoove_to_manychat.flow_env_key("ai_agents") == "MANYCHAT_FLOW_AI_AGENTS"
    assert smoove_to_manychat.flow_env_key("New Lead!") == "MANYCHAT_FLOW_NEW_LEAD"


# ------------------------------------------------- ManyChat's WhatsApp lookup gap
#
# ManyChat can only search the `phone` system field, but a contact created from a
# WhatsApp number leaves it empty — so the contact is invisible to the lookup while
# a second create is refused with "This WhatsApp ID already exists". Verified live
# against ManyChat on 2026-07-27; these tests pin the way out.


class _Resp:
    """The bits of a requests.Response the client actually reads."""

    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture
def live_client(monkeypatch):
    monkeypatch.setenv("MANYCHAT_API_KEY", "test-key")
    monkeypatch.setenv("MANYCHAT_CONSENT_PHRASE", "נתן את מספרו בטופס")
    return ManyChatClient()


def _route(client, monkeypatch, handler):
    """Route the client's HTTP at ``handler(method, url, json, params)``."""
    calls = []

    def fake(self, method, url, **kwargs):
        calls.append((method, url, kwargs.get("json"), kwargs.get("params")))
        return handler(method, url, kwargs.get("json"), kwargs.get("params"))

    monkeypatch.setattr(ManyChatClient, "_request", fake)
    return calls


def test_created_contact_gets_its_number_mirrored_into_phone(live_client, monkeypatch):
    # Without this second call the contact can never be found again.
    def handler(method, url, body, params):
        if url.endswith("createSubscriber"):
            return _Resp({"data": {"id": "137818625"}})
        return _Resp({"data": {"id": "137818625", "phone": body["phone"]}})

    calls = _route(live_client, monkeypatch, handler)
    assert live_client.create_subscriber("+972501234567", "דנה") == "137818625"

    update = [c for c in calls if c[1].endswith("updateSubscriber")]
    assert len(update) == 1, "the WhatsApp number must be mirrored into `phone`"
    assert update[0][2]["phone"] == "+972501234567"
    # No SMS opt-in is claimed on the lead's behalf just to make the search work.
    assert update[0][2]["has_opt_in_sms"] is False


def test_send_still_happens_when_mirroring_the_phone_fails(live_client, monkeypatch):
    def handler(method, url, body, params):
        if url.endswith("createSubscriber"):
            return _Resp({"data": {"id": "137818625"}})
        raise HttpError(400, url, '{"status":"error"}')

    _route(live_client, monkeypatch, handler)
    # The lead is waiting for a message; a bookkeeping call must not swallow it.
    assert live_client.create_subscriber("+972501234567", "דנה") == "137818625"


def test_existing_whatsapp_contact_is_found_not_recreated(live_client, monkeypatch):
    # A hit comes back as an object; a miss as an empty list, both with HTTP 200.
    def handler(method, url, body, params):
        if url.endswith("findBySystemField"):
            return _Resp({"data": {"id": "42", "phone": params["phone"]}})
        raise AssertionError("must not create a contact that already exists")

    _route(live_client, monkeypatch, handler)
    assert live_client.ensure_subscriber("+972501234567") == ("42", False)


def test_find_accepts_a_list_payload(live_client, monkeypatch):
    _route(live_client, monkeypatch,
           lambda *a: _Resp({"data": [{"id": "42"}]}))
    assert live_client.find_subscriber("+972501234567") == "42"


def test_missing_contact_is_a_200_with_an_empty_list(live_client, monkeypatch):
    _route(live_client, monkeypatch, lambda *a: _Resp({"data": []}))
    assert live_client.find_subscriber("+972501234567") is None


def test_already_exists_makes_us_look_again(live_client, monkeypatch):
    """The contact was created before we mirrored numbers, or by a parallel run."""
    seen = {"finds": 0}

    def handler(method, url, body, params):
        if url.endswith("findBySystemField"):
            seen["finds"] += 1
            # Invisible on the first look, findable once the create tells us it exists.
            return _Resp({"data": {"id": "77"} if seen["finds"] > 1 else []})
        raise HttpError(
            400, url,
            '{"details":{"messages":{"wa_id":{"message":'
            '["This WhatsApp ID already exists: 972501234567"]}}}}',
        )

    _route(live_client, monkeypatch, handler)
    assert live_client.ensure_subscriber("+972501234567") == ("77", False)


def test_unfindable_existing_contact_says_what_to_do(live_client, monkeypatch):
    def handler(method, url, body, params):
        if url.endswith("findBySystemField"):
            return _Resp({"data": []})
        raise HttpError(400, url, '{"message":"This WhatsApp ID already exists: 972…"}')

    _route(live_client, monkeypatch, handler)
    with pytest.raises(RuntimeError) as exc:
        live_client.ensure_subscriber("+972501234567")
    # A dead end the operator can act on, not a bare 500.
    assert "+972501234567" in str(exc.value)
    assert "Phone field" in str(exc.value)


# --------------------------------------------------------------------- the body


def test_parse_json_body():
    body = json.dumps({"f_name": "דנה", "cellphone": "0501234567", "msg": "ai_agents"})
    assert smoove_handler.parse_body(body)["msg"] == "ai_agents"


def test_parse_form_encoded_body():
    parsed = smoove_handler.parse_body("f_name=Dana&cellphone=0501234567&msg=ai_agents")
    assert parsed["cellphone"] == "0501234567"
    assert parsed["msg"] == "ai_agents"


def test_missing_fields_rejected():
    with pytest.raises(smoove_handler.Rejected) as exc:
        smoove_handler.handle(json.dumps({"f_name": "דנה"}), token="")
    assert exc.value.status == 400


# ---------------------------------------------------------------------- the auth


def test_open_when_no_token_configured(monkeypatch):
    # "no auth for now": absent SMOOVE_WEBHOOK_TOKEN means the endpoint is open.
    smoove_handler.verify_token("")  # does not raise


def test_token_required_once_configured(monkeypatch):
    monkeypatch.setenv("SMOOVE_WEBHOOK_TOKEN", "s3cret")
    with pytest.raises(smoove_handler.Rejected) as exc:
        smoove_handler.verify_token("")
    assert exc.value.status == 401
    with pytest.raises(smoove_handler.Rejected):
        smoove_handler.verify_token("wrong")
    smoove_handler.verify_token("s3cret")  # correct token does not raise


# --------------------------------------------------------------- idempotency


def test_duplicate_delivery_sends_once(read_log, monkeypatch):
    monkeypatch.setenv("MANYCHAT_FLOW_AI_AGENTS", "content_ai_agents")
    body = json.dumps({"f_name": "דנה", "cellphone": "0501234567", "msg": "ai_agents"})

    first = smoove_handler.handle(body, token="", dry_run=True)
    second = smoove_handler.handle(body, token="", dry_run=True)

    assert first["ok"] is True and first.get("duplicate") is None
    assert second["duplicate"] is True
    sends = [e for e in read_log() if e["action"] == "flow_sent"]
    assert len(sends) == 1, "a retried Smoove delivery must not send twice"


def test_failed_work_releases_the_claim(read_log, monkeypatch):
    monkeypatch.setenv("MANYCHAT_FLOW_AI_AGENTS", "content_ai_agents")
    calls = []

    def flaky(fn, phone, msg, dry_run=False):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("ManyChat 503")
        return {"ok": True}

    monkeypatch.setattr(smoove_to_manychat, "run", flaky)
    body = json.dumps({"f_name": "דנה", "cellphone": "0501234567", "msg": "ai_agents"})

    with pytest.raises(RuntimeError):
        smoove_handler.handle(body, token="", dry_run=True)
    # The claim was released, so the retry gets through rather than being deduped.
    result = smoove_handler.handle(body, token="", dry_run=True)
    assert result["ok"] is True
    assert len(calls) == 2


# ------------------------------------------------------- api gateway envelope


def test_lambda_handler_returns_200(monkeypatch):
    monkeypatch.setenv("MANYCHAT_FLOW_AI_AGENTS", "content_ai_agents")
    monkeypatch.setenv("WEBHOOK_DRY_RUN", "1")
    body = json.dumps({"f_name": "דנה", "cellphone": "0501234567", "msg": "ai_agents"})
    resp = smoove_handler.lambda_handler({"body": body, "headers": {}})
    assert resp["statusCode"] == 200


def test_lambda_handler_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("SMOOVE_WEBHOOK_TOKEN", "s3cret")
    body = json.dumps({"f_name": "דנה", "cellphone": "0501234567", "msg": "ai_agents"})
    resp = smoove_handler.lambda_handler(
        {"body": body, "headers": {"x-smoove-token": "nope"}}
    )
    assert resp["statusCode"] == 401


def test_lambda_handler_accepts_query_token(monkeypatch):
    monkeypatch.setenv("SMOOVE_WEBHOOK_TOKEN", "s3cret")
    monkeypatch.setenv("MANYCHAT_FLOW_AI_AGENTS", "content_ai_agents")
    monkeypatch.setenv("WEBHOOK_DRY_RUN", "1")
    body = json.dumps({"f_name": "דנה", "cellphone": "0501234567", "msg": "ai_agents"})
    resp = smoove_handler.lambda_handler(
        {"body": body, "headers": {}, "queryStringParameters": {"token": "s3cret"}}
    )
    assert resp["statusCode"] == 200

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

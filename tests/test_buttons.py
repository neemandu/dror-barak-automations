"""Tests for the ClickUp Button endpoint.

This endpoint sends quotes to Dror's clients. It is public, and — unlike the
API webhook — ClickUp does not sign Automation webhooks, so a header token is the
only thing standing between it and anyone who finds the URL. The auth tests here
are the important ones.
"""

from __future__ import annotations

import json

import pytest

from src import lambda_handler
from src.lib import actions

TOKEN = "auto-token-test"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATION_TOKEN", TOKEN)
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "idem.json"))
    monkeypatch.delenv("IDEMPOTENCY_TABLE", raising=False)
    yield


def body(task="t1", auto_id="a1", date="1750000000000"):
    return json.dumps({
        "auto_id": auto_id,
        "trigger_id": "tr1",
        "date": date,
        "payload": {"id": task, "name": "מכללת אלפא"},
    })


@pytest.fixture
def ran(monkeypatch):
    """Record which automation ran, without running it."""
    calls = []
    for key in actions.ACTIONS:
        monkeypatch.setitem(
            actions._RUNNERS, key,
            lambda cid, dry, _k=key: calls.append((_k, cid)) or {"ok": True},
        )
    monkeypatch.setattr(lambda_handler, "_comment", lambda *a, **k: None)
    return calls


# --------------------------------------------------------------------- auth


def test_correct_token_runs_the_action(ran):
    result = lambda_handler.handle_action("send_quote", body(), TOKEN)
    assert result["ok"] is True
    assert ran == [("send_quote", "t1")]


def test_wrong_token_is_rejected(ran):
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.handle_action("send_quote", body(), "guessed")
    assert exc.value.status == 401
    assert ran == [], "a quote must never be sent on an unauthenticated request"


def test_missing_token_is_rejected(ran):
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.handle_action("send_quote", body(), "")
    assert exc.value.status == 401
    assert ran == []


def test_unconfigured_token_fails_closed(monkeypatch, ran):
    # Missing config must not mean "let everyone in".
    monkeypatch.delenv("AUTOMATION_TOKEN", raising=False)
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.handle_action("send_quote", body(), "anything")
    assert exc.value.status == 500
    assert ran == []


# ------------------------------------------------------------------ routing


@pytest.mark.parametrize("key", sorted(actions.ACTIONS))
def test_every_button_reaches_its_automation(key, ran):
    lambda_handler.handle_action(key, body(), TOKEN)
    assert ran == [(key, "t1")]


def test_unknown_action_is_rejected(ran):
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.handle_action("delete_everything", body(), TOKEN)
    assert exc.value.status == 400
    assert ran == []


def test_payment_run_is_not_reachable_as_a_button():
    # Billing every active client is one mis-tap from disaster and the WhatsApp
    # messages cannot be unsent. It stays CLI-only, by design.
    assert "monthly_payment_requests" not in actions.ACTIONS
    assert "onboarding" not in actions.ACTIONS


def test_task_id_read_from_nested_and_flat_payloads():
    assert actions.task_id_of(json.loads(body())) == "t1"
    assert actions.task_id_of({"id": "flat1"}) == "flat1"
    assert actions.task_id_of({}) is None


def test_payload_without_a_task_is_rejected(ran):
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.handle_action("send_quote", "{}", TOKEN)
    assert exc.value.status == 400


def test_malformed_body_is_rejected(ran):
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.handle_action("send_quote", "not json", TOKEN)
    assert exc.value.status == 400


# ------------------------------------------------------------- idempotency


def test_a_retried_click_only_sends_once(ran):
    b = body()
    lambda_handler.handle_action("send_quote", b, TOKEN)
    second = lambda_handler.handle_action("send_quote", b, TOKEN)
    assert ran == [("send_quote", "t1")], "one press must not send two quotes"
    assert second["duplicate"] is True


def test_a_second_deliberate_press_does_send_again(ran):
    # Dror revises the quote and presses again. This MUST work -- over-zealous
    # de-duplication would silently refuse to send the corrected quote.
    lambda_handler.handle_action("send_quote", body(date="1750000000000"), TOKEN)
    lambda_handler.handle_action("send_quote", body(date="1750000999999"), TOKEN)
    assert len(ran) == 2


def test_different_clients_do_not_block_each_other(ran):
    lambda_handler.handle_action("send_quote", body(task="t1"), TOKEN)
    lambda_handler.handle_action("send_quote", body(task="t2"), TOKEN)
    assert [c for _, c in ran] == ["t1", "t2"]


def test_a_failed_press_can_be_retried(monkeypatch):
    attempts = []

    def flaky(cid, dry):
        attempts.append(cid)
        if len(attempts) == 1:
            raise RuntimeError("Fillout 503")
        return {"ok": True}

    monkeypatch.setitem(actions._RUNNERS, "send_quote", flaky)
    monkeypatch.setattr(lambda_handler, "_comment", lambda *a, **k: None)

    b = body()
    with pytest.raises(RuntimeError):
        lambda_handler.handle_action("send_quote", b, TOKEN)
    lambda_handler.handle_action("send_quote", b, TOKEN)  # same press, retried
    assert len(attempts) == 2


# --------------------------------------------------------------- feedback


def test_success_is_reported_back_on_the_task(monkeypatch):
    monkeypatch.setitem(actions._RUNNERS, "send_quote", lambda c, d: {"ok": True})
    said = []
    monkeypatch.setattr(lambda_handler, "_comment",
                        lambda tid, msg, dry: said.append((tid, msg)))
    lambda_handler.handle_action("send_quote", body(), TOKEN)
    assert said == [("t1", "✅ נשלחה הצעת מחיר ללקוח")]


def test_failure_is_reported_back_on_the_task(monkeypatch):
    def boom(cid, dry):
        raise RuntimeError("Fillout is down")

    monkeypatch.setitem(actions._RUNNERS, "send_quote", boom)
    said = []
    monkeypatch.setattr(lambda_handler, "_comment",
                        lambda tid, msg, dry: said.append(msg))
    with pytest.raises(RuntimeError):
        lambda_handler.handle_action("send_quote", body(), TOKEN)
    # Dror pressed a button; silence would leave him unsure if the quote went out.
    assert "נכשל" in said[0]


def test_a_broken_comment_does_not_lose_the_work(monkeypatch):
    monkeypatch.setitem(actions._RUNNERS, "send_quote", lambda c, d: {"ok": True})

    def bad_crm(*a, **k):
        raise RuntimeError("ClickUp 500")

    monkeypatch.setattr(lambda_handler, "_comment", lambda_handler._comment)
    monkeypatch.setattr("src.lib.clients.crm.CrmClient.append_automation_log", bad_crm)
    # The quote was sent. Failing here would make ClickUp retry and send it twice.
    result = lambda_handler.handle_action("send_quote", body(), TOKEN)
    assert result["ok"] is True


# ------------------------------------------------- api gateway integration


def test_action_route_is_reached_through_api_gateway(monkeypatch, ran):
    resp = lambda_handler.lambda_handler({
        "rawPath": "/dev/action",
        "requestContext": {"http": {"path": "/dev/action"}},
        "queryStringParameters": {"action": "send_quote"},
        "headers": {"x-automation-token": TOKEN},
        "body": body(),
    })
    assert resp["statusCode"] == 200
    assert ran == [("send_quote", "t1")]


def test_forged_button_press_gets_401_through_api_gateway(ran):
    resp = lambda_handler.lambda_handler({
        "rawPath": "/dev/action",
        "requestContext": {"http": {"path": "/dev/action"}},
        "queryStringParameters": {"action": "send_quote"},
        "headers": {"x-automation-token": "nope"},
        "body": body(),
    })
    assert resp["statusCode"] == 401
    assert ran == []

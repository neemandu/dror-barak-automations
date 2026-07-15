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


def test_task_id_read_from_every_payload_shape_clickup_might_send():
    # Current format: task nested under `payload`.
    assert actions.task_id_of(json.loads(body())) == "t1"
    # Legacy format: task at the top level.
    assert actions.task_id_of({"id": "flat1"}) == "flat1"
    # A custom body written by hand in the Automation UI.
    assert actions.task_id_of({"task_id": "custom1"}) == "custom1"
    assert actions.task_id_of({"payload": {"task_id": "nested1"}}) == "nested1"
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


def test_clickup_test_button_gets_a_helpful_200_not_a_scary_400(ran):
    # ClickUp's Automation editor Test button sends no task, because it cannot
    # know which task the real button would be pressed on. A 400 here reads as
    # "your button is broken" precisely when someone is checking it works.
    result = lambda_handler.handle_action(
        "send_quote", '{"body":"Test message from ClickUp Webhooks Service"}', TOKEN
    )
    assert result["ok"] is True
    assert result["test"] is True
    assert ran == [], "a test ping must not send a real quote"


def test_test_ping_still_requires_authentication(ran):
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.handle_action(
            "send_quote", '{"body":"Test message from ClickUp Webhooks Service"}', "wrong"
        )
    assert exc.value.status == 401


def test_a_real_payload_is_never_mistaken_for_a_test_ping(ran):
    # Only a lone `body` string counts. A real task payload must still run.
    lambda_handler.handle_action("send_quote", body(), TOKEN)
    assert ran == [("send_quote", "t1")]


def test_body_key_alongside_a_task_is_not_a_test_ping(ran):
    payload = json.dumps({"body": "note", "payload": {"id": "t7"}})
    lambda_handler.handle_action("send_quote", payload, TOKEN)
    assert ran == [("send_quote", "t7")]


def test_dry_run_still_comments_but_says_it_was_a_test(monkeypatch):
    # Dry-run exists to avoid creating Drive folders and messaging clients -- not
    # to hide from Dror whether his button worked. A press with no visible result
    # is indistinguishable from a broken button.
    # The comment posts for real, so the live client needs its config present.
    monkeypatch.setenv("CLICKUP_API_TOKEN", "pk_test")
    monkeypatch.setenv("CLICKUP_LIST_ID", "123")
    monkeypatch.setitem(actions._RUNNERS, "send_quote", lambda c, d: {"ok": True})
    said = []
    monkeypatch.setattr(
        "src.lib.clients.crm.CrmClient.append_automation_log",
        lambda self, tid, msg: said.append((self.dry_run, msg)),
    )
    lambda_handler.handle_action("send_quote", body(), TOKEN, dry_run=True)

    assert len(said) == 1, "dry-run must still tell Dror what happened"
    posted_with_dry_run, msg = said[0]
    assert posted_with_dry_run is False, "the comment itself must really post"
    assert "הרצת ניסיון" in msg, "and must say nothing actually went out"

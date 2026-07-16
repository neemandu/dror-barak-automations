"""Tests for the ClickUp webhook Lambda: signature, idempotency, routing.

The endpoint is public and the work behind it is not retractable — a duplicate
`חתם` runs onboarding twice, creating two Drive folders and two Morning clients.
So the duplicate and failure paths matter more than the happy path here.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from src import lambda_handler
from src.lib import idempotency

SECRET = "whsec-test"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLICKUP_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "idem.json"))
    monkeypatch.delenv("IDEMPOTENCY_TABLE", raising=False)  # use the file store
    monkeypatch.delenv("CLICKUP_TASKS_LIST_ID", raising=False)
    yield


def sign(body: str) -> str:
    return hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()


def payload(event="taskUpdated", task="t1", hist_id="h1", after=None, **extra):
    body = {
        "event": event,
        "task_id": task,
        "webhook_id": "wh1",
        "history_items": [{"id": hist_id, "field": "status", "after": after}],
        **extra,
    }
    return json.dumps(body, ensure_ascii=False)


# ------------------------------------------------------------------ signature


def test_valid_signature_is_accepted():
    body = payload(after={"name": "נשלח שאלון"})
    lambda_handler.verify_signature(body, sign(body))  # does not raise


def test_bad_signature_is_rejected():
    body = payload()
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.verify_signature(body, "deadbeef")
    assert exc.value.status == 401


def test_missing_signature_is_rejected():
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.verify_signature(payload(), "")
    assert exc.value.status == 401


def test_tampered_body_fails_even_with_a_real_signature():
    # The attack this defends: capture a genuine delivery, change the task id.
    good = payload(task="t1")
    sig = sign(good)
    tampered = payload(task="t999")
    with pytest.raises(lambda_handler.Rejected):
        lambda_handler.verify_signature(tampered, sig)


def test_no_secret_configured_fails_closed(monkeypatch):
    # Serving unsigned traffic because config is missing would make the endpoint
    # an open trigger for onboarding.
    monkeypatch.delenv("CLICKUP_WEBHOOK_SECRET", raising=False)
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.verify_signature("{}", "anything")
    assert exc.value.status == 500


def test_unsigned_request_never_reaches_an_automation(monkeypatch):
    called = []
    monkeypatch.setattr(lambda_handler, "route", lambda *a, **k: called.append(1))
    with pytest.raises(lambda_handler.Rejected):
        lambda_handler.handle(payload(), "wrong")
    assert called == []


# ---------------------------------------------------------------- idempotency


def test_same_delivery_twice_runs_the_work_once(monkeypatch):
    runs = []
    monkeypatch.setattr(lambda_handler, "route", lambda p, dry_run=False: runs.append(p["task_id"]) or {"ok": 1})

    body = payload(after={"name": "חתם"})
    first = lambda_handler.handle(body, sign(body))
    second = lambda_handler.handle(body, sign(body))

    assert runs == ["t1"], "onboarding must not run twice for one delivery"
    assert first.get("duplicate") is None
    assert second["duplicate"] is True


def test_a_different_change_to_the_same_task_is_not_a_duplicate(monkeypatch):
    runs = []
    monkeypatch.setattr(lambda_handler, "route", lambda p, dry_run=False: runs.append(p) or {})
    a = payload(hist_id="h1", after={"name": "נשלח שאלון"})
    b = payload(hist_id="h2", after={"name": "חתם"})
    lambda_handler.handle(a, sign(a))
    lambda_handler.handle(b, sign(b))
    assert len(runs) == 2


def test_failed_work_releases_the_claim_so_a_retry_can_succeed(monkeypatch):
    attempts = []

    def flaky(p, dry_run=False):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Drive timed out")
        return {"ok": True}

    monkeypatch.setattr(lambda_handler, "route", flaky)
    body = payload(after={"name": "חתם"})

    with pytest.raises(RuntimeError):
        lambda_handler.handle(body, sign(body))
    # ClickUp retries the same delivery; holding the claim would skip it forever.
    result = lambda_handler.handle(body, sign(body))
    assert result["ok"] is True
    assert len(attempts) == 2


def test_event_key_is_stable_and_distinct():
    p1 = json.loads(payload(hist_id="h1"))
    p2 = json.loads(payload(hist_id="h1"))
    p3 = json.loads(payload(hist_id="h2"))
    assert idempotency.event_key(p1) == idempotency.event_key(p2)
    assert idempotency.event_key(p1) != idempotency.event_key(p3)


def test_event_key_falls_back_to_the_body_when_there_is_no_history():
    body = '{"event":"taskCreated","task_id":"t9"}'
    key = idempotency.event_key(json.loads(body), body)
    assert key.startswith("evt:taskCreated:t9:")


def test_claim_is_won_once(tmp_path, monkeypatch):
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "i.json"))
    assert idempotency.claim("k1") is True
    assert idempotency.claim("k1") is False
    idempotency.release("k1")
    assert idempotency.claim("k1") is True


def test_expired_claim_can_be_reclaimed(tmp_path, monkeypatch):
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "i.json"))
    assert idempotency.claim("k2", ttl=-1) is True
    assert idempotency.claim("k2") is True  # the first has already expired


def test_business_guard_key_is_per_automation_and_client():
    assert idempotency.guard("onboarding", "c1") != idempotency.guard("onboarding", "c2")
    assert idempotency.guard("onboarding", "c1") != idempotency.guard("send_quote", "c1")


# -------------------------------------------------------------------- routing


@pytest.fixture
def crm_says(monkeypatch):
    """Pin the client's current secondary status as ClickUp would report it.

    Routing reads current state rather than parsing the payload, so this is the
    thing that decides which automation fires.
    """

    def _set(sub_status):
        monkeypatch.setattr(
            lambda_handler, "_sub_status_of", lambda task_id, dry_run: sub_status
        )

    return _set


def test_onboarding_fires_on_signed(monkeypatch, crm_says):
    from src.automations import onboarding

    crm_says("signed")
    seen = []
    monkeypatch.setattr(onboarding, "run", lambda cid, dry_run=False: seen.append(cid) or {})
    lambda_handler.route(json.loads(payload()))
    assert seen == ["t1"]


def test_unrelated_status_change_does_nothing(crm_says):
    crm_says("in_work")
    result = lambda_handler.route(json.loads(payload()))
    assert "ignored" in result


def test_onboarding_runs_once_even_for_two_distinct_signed_events(monkeypatch, crm_says):
    # Dror flips חתם -> בעבודה -> חתם: two real deliveries, two different history
    # ids, so delivery dedup does NOT catch this. Onboarding must still run once,
    # or the client gets two Drive folders and two Morning records.
    from src.automations import onboarding

    crm_says("signed")
    runs = []
    monkeypatch.setattr(onboarding, "run", lambda cid, dry_run=False: runs.append(cid) or {})

    a = payload(hist_id="h1")
    b = payload(hist_id="h2")
    lambda_handler.handle(a, sign(a))
    second = lambda_handler.handle(b, sign(b))

    assert runs == ["t1"], "onboarding must not run twice for the same client"
    assert "already ran" in json.dumps(second, ensure_ascii=False)


def test_failed_onboarding_can_be_retried(monkeypatch, crm_says):
    # The business guard must not permanently block a client whose first
    # onboarding attempt died halfway.
    from src.automations import onboarding

    crm_says("signed")
    attempts = []

    def flaky(cid, dry_run=False):
        attempts.append(cid)
        if len(attempts) == 1:
            raise RuntimeError("Drive 503")
        return {"ok": True}

    monkeypatch.setattr(onboarding, "run", flaky)
    a = payload(hist_id="h1")
    with pytest.raises(RuntimeError):
        lambda_handler.handle(a, sign(a))

    b = payload(hist_id="h2")
    lambda_handler.handle(b, sign(b))
    assert len(attempts) == 2


def test_task_created_saves_the_lead(monkeypatch):
    from src.automations import lead_to_contacts

    seen = []
    monkeypatch.setattr(lead_to_contacts, "run", lambda cid, dry_run=False: seen.append(cid) or {})
    lambda_handler.route(json.loads(payload(event="taskCreated", after={"status": "ליד"})))
    assert seen == ["t1"]


def test_a_task_in_the_work_list_goes_to_claude_not_the_crm(monkeypatch):
    from src.automations import clickup_to_claude

    monkeypatch.setenv("CLICKUP_TASKS_LIST_ID", "999")
    seen = []
    monkeypatch.setattr(clickup_to_claude, "run", lambda tid, dry_run=False: seen.append(tid) or {})
    lambda_handler.route(json.loads(payload(event="taskCreated", list_id="999")))
    assert seen == ["t1"], "משימות tasks must not be treated as new leads"


def test_payload_without_task_id_is_rejected():
    with pytest.raises(lambda_handler.Rejected) as exc:
        lambda_handler.route({"event": "taskCreated"})
    assert exc.value.status == 400


# ------------------------------------------------------- api gateway envelope


def test_lambda_handler_returns_200_for_a_good_request(monkeypatch):
    monkeypatch.setattr(lambda_handler, "route", lambda p, dry_run=False: {"ok": 1})
    body = payload(after={"name": "חתם"})
    resp = lambda_handler.lambda_handler(
        {"body": body, "headers": {"X-Signature": sign(body)}}
    )
    assert resp["statusCode"] == 200


def test_lambda_handler_returns_401_for_a_forged_request():
    resp = lambda_handler.lambda_handler(
        {"body": payload(), "headers": {"x-signature": "nope"}}
    )
    assert resp["statusCode"] == 401


def test_lambda_handler_returns_500_so_clickup_retries(monkeypatch):
    def boom(p, dry_run=False):
        raise RuntimeError("Morning is down")

    monkeypatch.setattr(lambda_handler, "route", boom)
    body = payload(after={"name": "חתם"})
    resp = lambda_handler.lambda_handler(
        {"body": body, "headers": {"x-signature": sign(body)}}
    )
    assert resp["statusCode"] == 500


def test_base64_body_is_decoded_before_signing(monkeypatch):
    import base64

    monkeypatch.setattr(lambda_handler, "route", lambda p, dry_run=False: {"ok": 1})
    body = payload(after={"name": "חתם"})
    resp = lambda_handler.lambda_handler({
        "body": base64.b64encode(body.encode()).decode(),
        "isBase64Encoded": True,
        "headers": {"x-signature": sign(body)},
    })
    assert resp["statusCode"] == 200

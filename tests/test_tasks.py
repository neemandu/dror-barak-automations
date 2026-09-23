"""Background tasks: inline off Lambda, reported when they fail, routed on Lambda."""

from __future__ import annotations

import pytest

from src import lambda_handler
from src.lib import tasks


def test_off_lambda_a_task_runs_inline(monkeypatch):
    from src.automations import social_prep

    monkeypatch.setattr(social_prep, "run", lambda client_id, dry_run=False: {"ran": client_id})
    out = tasks.dispatch("social_prep", client_id="42", dry_run=True)
    assert out == {"inline": True, "result": {"ran": "42"}}


def test_on_lambda_a_task_is_queued_not_run(monkeypatch):
    calls = []

    class FakeLambda:
        def invoke(self, **kw):
            calls.append(kw)

    import boto3

    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "dror-webhook-test")
    monkeypatch.setattr(boto3, "client", lambda name: FakeLambda())
    out = tasks.dispatch("strategy_bot", client_id="42")
    assert out["queued"] is True
    assert calls[0]["FunctionName"] == "dror-webhook-test" and calls[0]["InvocationType"] == "Event"


def test_a_failed_task_is_reported_where_dror_looks(read_log, monkeypatch):
    from src.automations import strategy_bot

    def boom(client_id, dry_run=False):
        raise RuntimeError("Drive is down")

    monkeypatch.setattr(strategy_bot, "run", boom)
    with pytest.raises(RuntimeError):
        tasks.run("strategy_bot", {"client_id": "42"}, dry_run=True)
    entry = next(e for e in read_log() if e["action"] == "task_failed")
    assert entry["status"] == "error" and "Drive is down" in entry["detail"]


def test_the_webhook_lambda_runs_a_task_event(monkeypatch):
    from src.automations import social_prep

    monkeypatch.setattr(social_prep, "run", lambda client_id, dry_run=False: {"ok": client_id})
    out = lambda_handler.lambda_handler({"task": "social_prep", "args": {"client_id": "7"}, "dry_run": True})
    assert out == {"ok": True, "task": "social_prep", "result": {"ok": "7"}}


def test_unknown_tasks_are_refused():
    with pytest.raises(KeyError):
        tasks.dispatch("delete_everything")

"""The bots as employees: who a task is for, when it runs, what each one gets."""

from __future__ import annotations

import json

import pytest

from src import lambda_handler
from src.automations import clickup_to_claude as bot
from src.lib import workers
from src.lib.agent_tools import Toolbox
from src.lib.clients.clickup import ClickUpClient

# The dropdown as ClickUp returns it (checked live 25.9: value is the option's index).
OPTIONS = [{"id": "o0", "name": "כותב תוכן", "orderindex": 0},
           {"id": "o1", "name": "אנליסט רשתות", "orderindex": 1},
           {"id": "o2", "name": "מנהל קמפיינים", "orderindex": 2},
           {"id": "o3", "name": "עוזר אישי", "orderindex": 3}]


def task_with(value, **extra):
    return {"id": "t1", "name": "משימה", "description": "",
            "custom_fields": [{"id": "f", "name": "עובד", "type": "drop_down",
                               "type_config": {"options": OPTIONS}, "value": value}], **extra}


# ------------------------------------------------------------ who


@pytest.mark.parametrize("value,expected", [
    (0, "כותב תוכן"), (2, "מנהל קמפיינים"), ("o3", "עוזר אישי"), (None, None), ("", None),
])
def test_the_employee_is_read_from_the_dropdown(value, expected):
    worker = workers.worker_of(task_with(value))
    assert (worker.name if worker else None) == expected


def test_a_list_without_the_field_keeps_the_old_behaviour():
    assert workers.worker_of({"custom_fields": []}) is workers.DEFAULT
    assert not workers.has_field({"custom_fields": []})


def test_every_dropdown_option_is_an_employee():
    assert {o["name"] for o in OPTIONS} == set(workers.WORKERS)


# ------------------------------------------------------------ when (the webhook)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "idem.json"))
    monkeypatch.delenv("IDEMPOTENCY_TABLE", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    ran = []
    monkeypatch.setattr(bot, "run", lambda task_id, dry_run=False, **k: ran.append(task_id) or {"ok": 1})
    return ran


def _route(monkeypatch, event, task):
    monkeypatch.setattr(ClickUpClient, "get_task", lambda self, tid: task)
    return lambda_handler.route({"event": event, "task_id": "t1"}, dry_run=True, source="tasks")


def test_a_task_for_a_person_is_left_alone(env, monkeypatch):
    out = _route(monkeypatch, "taskCreated", task_with(None))
    assert "person" in out["ignored"] and env == []


def test_setting_the_employee_later_hands_the_task_over_once(env, monkeypatch):
    assert "ignored" in _route(monkeypatch, "taskCreated", task_with(None))
    out = _route(monkeypatch, "taskUpdated", task_with(1))
    assert out["worker"] == "אנליסט רשתות" and env == ["t1"]
    # the bot's own status change, Dror editing the description: updates too
    assert "already" in _route(monkeypatch, "taskUpdated", task_with(1))["ignored"]
    assert env == ["t1"]


def test_created_with_an_employee_runs_once_even_if_an_update_follows(env, monkeypatch):
    _route(monkeypatch, "taskCreated", task_with(0))
    _route(monkeypatch, "taskUpdated", task_with(0))
    assert env == ["t1"]


def test_updates_on_a_list_without_the_field_never_run(env, monkeypatch):
    out = _route(monkeypatch, "taskUpdated", {"id": "t1", "custom_fields": []})
    assert "ignored" in out and env == []


# ------------------------------------------------------------ what (the run)


class _Board:
    def __init__(self, monkeypatch, task):
        self.statuses, self.posted = [], []
        board = self
        monkeypatch.setattr(ClickUpClient, "get_task", lambda s, tid: task)
        monkeypatch.setattr(ClickUpClient, "list_comments", lambda s, tid: [])
        monkeypatch.setattr(ClickUpClient, "comment", lambda s, tid, text: board.posted.append(text) or {})
        monkeypatch.setattr(ClickUpClient, "set_status",
                            lambda s, tid, status: board.statuses.append(status) or {})


def test_the_task_moves_to_working_then_to_drors_review(monkeypatch):
    board = _Board(monkeypatch, task_with(0))
    bot.run("t1", dry_run=True)
    assert board.statuses == ["in progress", "לבדיקה של דרור"]
    assert "· כותב תוכן" in board.posted[0]


def test_a_failed_run_leaves_the_task_out_of_review(monkeypatch):
    from src.lib.clients.anthropic_ai import AnthropicClient

    board = _Board(monkeypatch, task_with(0))
    monkeypatch.setattr(AnthropicClient, "create_message",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("overloaded")))
    with pytest.raises(RuntimeError):
        bot.run("t1", dry_run=True)
    assert "לבדיקה של דרור" not in board.statuses
    assert board.posted[-1].startswith("❌")


def test_statuses_are_left_alone_on_a_list_without_the_field(monkeypatch):
    board = _Board(monkeypatch, {"id": "t1", "name": "x", "custom_fields": []})
    bot.run("t1", dry_run=True)
    assert board.statuses == []


def test_each_employee_gets_its_own_job_description(monkeypatch):
    from src.lib.clients.anthropic_ai import AnthropicClient

    systems = []
    real = AnthropicClient.create_message
    monkeypatch.setattr(AnthropicClient, "create_message",
                        lambda self, m, **kw: systems.append(kw["system"]) or real(self, m, **kw))
    _Board(monkeypatch, task_with(2))
    bot.run("t1", dry_run=True)
    assert workers.CAMPAIGNS.job in systems[0] and workers.WRITER.job not in systems[0]


def test_the_meta_tool_exists_only_for_a_client_with_an_ad_account():
    assert "meta_ads_insights" not in {d["name"] for d in Toolbox().definitions}
    box = Toolbox(meta_account="act_1")
    assert "meta_ads_insights" in {d["name"] for d in box.definitions}
    assert "Unknown tool" in Toolbox().run("meta_ads_insights", {"since": "a", "until": "b"})[0]


def test_the_meta_tool_reads_only_the_tasks_client(monkeypatch):
    from src.lib.clients.meta_ads import MetaAdsClient

    asked = []
    monkeypatch.setattr(MetaAdsClient, "__init__", lambda self, dry_run=False: setattr(self, "dry_run", True) or setattr(self, "calls", []))
    real = MetaAdsClient.insights
    monkeypatch.setattr(MetaAdsClient, "insights",
                        lambda self, act, **kw: asked.append(act) or real(self, act, **kw))
    text, is_error = Toolbox(meta_account="act_777").run(
        "meta_ads_insights", {"since": "2026-09-01", "until": "2026-09-07", "act": "act_999"})
    # an extra argument naming another account is refused, not obeyed
    assert is_error and asked == []
    text, is_error = Toolbox(meta_account="act_777").run(
        "meta_ads_insights", {"since": "2026-09-01", "until": "2026-09-07"})
    assert not is_error and asked == ["act_777"]
    assert "campaigns" in json.loads(text) or "totals" in json.loads(text)

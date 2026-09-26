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


# ------------------------------------------------------------ switching employee


def test_switching_the_employee_hands_the_task_over_again(env, monkeypatch):
    _route(monkeypatch, "taskUpdated", task_with(0))       # copywriter
    _route(monkeypatch, "taskUpdated", task_with(0))       # a status change: nothing
    out = _route(monkeypatch, "taskUpdated", task_with(2))  # -> campaign manager
    assert out["worker"] == "מנהל קמפיינים"
    out = _route(monkeypatch, "taskUpdated", task_with(0))  # and back
    assert out["worker"] == "כותב תוכן"
    assert env == ["t1", "t1", "t1"]


# ------------------------------------------------------------ actions: email


DOC = "https://docs.google.com/document/d/DOC1/edit"
DROR = {"id": 0}          # ClickUpClient.me() is "0" in dry-run
SOMEONE = {"id": 555}


def _card(cid, draft_id="dr1"):
    return {"id": cid, "comment_text": bot.card_text({"id": draft_id, "to": "client@x.co", "subject": "המלצות"}),
            "user": DROR}


class _Thread:
    def __init__(self, monkeypatch, replies):
        root = {"id": "C1", "comment_text": f"🤖 Claude: גרסה 1\n{DOC}\n\nהמלצות", "reply_count": len(replies)}
        self.replied = []
        board = self
        monkeypatch.setattr(ClickUpClient, "list_comments", lambda s, tid: [root])
        monkeypatch.setattr(ClickUpClient, "list_replies", lambda s, cid: list(replies))
        monkeypatch.setattr(ClickUpClient, "reply", lambda s, cid, text: board.replied.append(text) or {})
        monkeypatch.setattr(ClickUpClient, "get_task", lambda s, tid: task_with(2))


def test_drors_send_sends_the_pending_email(monkeypatch, read_log):
    from src.lib.clients.anthropic_ai import AnthropicClient

    monkeypatch.setattr(AnthropicClient, "create_message", lambda *a, **k: pytest.fail("no model call"))
    thread = _Thread(monkeypatch, [_card("K1"), {"id": "A1", "comment_text": "שלח", "user": DROR}])
    out = bot.run("t1", comment="שלח", comment_id="A1", dry_run=True)
    assert out["sent"]["id"] == "dr1"
    assert thread.replied[-1].startswith("✅ המייל נשלח אל client@x.co")
    assert any(e["action"] == "client_email_sent" for e in read_log())


def test_nobody_but_dror_can_approve_a_send(monkeypatch, read_log):
    thread = _Thread(monkeypatch, [_card("K1"), {"id": "A1", "comment_text": "שלח", "user": SOMEONE}])
    out = bot.run("t1", comment="שלח", comment_id="A1", dry_run=True)
    assert "refused" in out and "רק דרור" in thread.replied[-1]
    assert any(e["action"] == "send_refused" for e in read_log())


def test_an_email_is_sent_once(monkeypatch):
    sent = {"id": "S1", "comment_text": "✅ המייל נשלח אל client@x.co: המלצות\n[draft:dr1]"}
    thread = _Thread(monkeypatch, [_card("K1"), sent, {"id": "A2", "comment_text": "שלח", "user": DROR}])
    out = bot.run("t1", comment="שלח", comment_id="A2", dry_run=True)
    assert "ignored" in out and "אין בשרשור" in thread.replied[-1]


def test_send_with_nothing_pending_is_not_fed_to_claude(monkeypatch):
    from src.lib.clients.anthropic_ai import AnthropicClient

    monkeypatch.setattr(AnthropicClient, "create_message", lambda *a, **k: pytest.fail("no model call"))
    _Thread(monkeypatch, [{"id": "A1", "comment_text": "שלח", "user": DROR}])
    assert "ignored" in bot.run("t1", comment="שלח", comment_id="A1", dry_run=True)


def test_a_draft_the_agent_made_becomes_a_card_in_the_thread(monkeypatch):
    from src.lib.clients.anthropic_ai import AnthropicClient

    posted, replied = [], []
    monkeypatch.setattr(ClickUpClient, "get_task", lambda s, tid: task_with(2))
    monkeypatch.setattr(ClickUpClient, "list_comments", lambda s, tid: [])
    monkeypatch.setattr(ClickUpClient, "comment", lambda s, tid, text: posted.append(text) or {"id": "NEW"})
    monkeypatch.setattr(ClickUpClient, "reply", lambda s, cid, text: replied.append((cid, text)) or {})

    script = [
        {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "u1", "name": "gmail_create_draft",
                                                 "input": {"to": "c@x.co", "subject": "המלצות", "body": "שלום"}}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "# המלצות\nהכנתי טיוטה."}]},
    ]
    monkeypatch.setattr(AnthropicClient, "create_message", lambda *a, **k: script.pop(0))

    def fake_post(url, *, gmail=False, **kw):
        class R:
            def json(self):
                return {"id": "dr9"}
        return R()

    monkeypatch.setattr(Toolbox, "_post", staticmethod(fake_post))
    monkeypatch.setattr(bot, "Toolbox", lambda **kw: Toolbox(**{**kw, "dry_run": False}))
    bot.run("t1", dry_run=True)
    assert replied and replied[0][0] == "NEW"
    assert replied[0][1].startswith("📧 מייל מוכן לשליחה") and "[draft:dr9]" in replied[0][1]


def test_the_bots_cards_and_confirmations_never_count_as_feedback():
    for text in (bot.card_text({"id": "x", "to": "a", "subject": "b"}), "✅ המייל נשלח אל a: b"):
        assert bot.instruction_in(text) is None
        assert text.startswith(bot.BOT_PREFIXES)


def test_a_decorated_field_name_is_still_found():
    # Dror names fields with emoji ("📋 תבניות"); the lookup must not go quiet.
    from src.automations import client_templates

    task = {"custom_fields": [{"name": "📋 תבניות", "type": "labels", "value": ["x1"],
                               "type_config": {"options": [{"id": "x1", "label": "מעקב פאנלים"}]}},
                              {"name": "👤 עובד", "type": "drop_down", "value": 1,
                               "type_config": {"options": OPTIONS}}]}
    assert client_templates.picked(task) == ["מעקב פאנלים"]
    assert workers.worker_of(task).name == "אנליסט רשתות"

"""The signing follow-up as a ClickUp task: automatic, but Dror's to edit or cancel."""

from __future__ import annotations

import time

import pytest

from src.automations import sign_reminders
from src.lib import reminder_tasks, signing
from src.lib.clients.clickup import ClickUpClient
from src.lib.http import HttpError

DAY_MS = 24 * 60 * 60 * 1000


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "i.json"))
    monkeypatch.setenv("IDEMPOTENCY_TABLE", "")
    monkeypatch.setenv("CLICKUP_TASKS_LIST_ID", "L-tasks")
    monkeypatch.setenv("CLICKUP_API_TOKEN", "test-token")  # the client is built live; its calls are faked


class _ClickUp:
    """One follow-up task, as ClickUp would hold it."""

    def __init__(self, monkeypatch, task=None):
        self.task = task
        self.comments, self.statuses, self.created, self.updated = [], [], [], []
        cu = self

        def get_task(s, tid):
            if cu.task is None:
                raise HttpError(404, "task", "not found")
            return cu.task

        monkeypatch.setattr(ClickUpClient, "get_task", get_task)
        monkeypatch.setattr(ClickUpClient, "comment", lambda s, tid, text: cu.comments.append(text) or {})
        monkeypatch.setattr(ClickUpClient, "set_status", lambda s, tid, st: cu.statuses.append(st) or {})
        monkeypatch.setattr(ClickUpClient, "get_list_fields", lambda s, lid: [
            {"id": "rel", "name": "לקוח", "type": "list_relationship"}])
        monkeypatch.setattr(ClickUpClient, "create_task",
                            lambda s, lid, name, **kw: cu.created.append((lid, name, kw)) or {"id": "R1"})
        monkeypatch.setattr(ClickUpClient, "update_task",
                            lambda s, tid, **kw: cu.updated.append((tid, kw)) or {})


def _task(due_in_days, *, text="היי רונית\n\nטקסט שדרור ערך", closed=False):
    return {"id": "R1", "name": "תזכורת חתימה: רונית",
            "due_date": str(int(time.time() * 1000 + due_in_days * DAY_MS)),
            "text_content": text,
            "status": {"status": "complete" if closed else "to do", "type": "closed" if closed else "open"}}


def _pending_client(monkeypatch, task_id="R1"):
    from src.lib.clients.crm import CrmClient

    monkeypatch.setattr(CrmClient, "list_by_sub_status", lambda self, s: [
        {"id": "c1", "name": "רונית כהן", "first_name": "רונית", "email": "r@x.co"}])
    signing.mark_pending("c1")
    if task_id:
        signing.set_reminder_task("c1", task_id)


def _sent(monkeypatch):
    from src.lib import emails

    sent = []
    monkeypatch.setattr(emails, "send_template", lambda name, to, **k: sent.append((name, to, k)))
    return sent


# ------------------------------------------------------------ opening the task


def test_sending_a_contract_opens_a_task_due_in_three_days_with_the_text(monkeypatch):
    cu = _ClickUp(monkeypatch)
    now = time.time()
    task_id = reminder_tasks.open_task("c1", {"name": "רונית כהן", "first_name": "רונית"}, now=now)
    assert task_id == "R1"
    lid, name, kw = cu.created[0]
    assert lid == "L-tasks" and name == "תזכורת חתימה: רונית כהן"
    assert kw["description"].startswith("היי רונית") and "בהמשך לפגישה שלנו" in kw["description"]
    assert kw["due_date"] == int(now * 1000 + 3 * DAY_MS)
    assert kw["custom_fields"] == [{"id": "rel", "value": {"add": ["c1"]}}]
    assert cu.comments and cu.comments[0].startswith("🤖")  # explains itself, never feedback


def test_resending_a_contract_moves_the_open_task_instead_of_opening_another(monkeypatch):
    cu = _ClickUp(monkeypatch, task=_task(1))
    _pending_client(monkeypatch)
    signing.mark_pending("c1")  # the re-send rewrites the record ...
    assert signing.get_pending("c1")["reminder_task_id"] == "R1"  # ... but keeps the task
    reminder_tasks.open_task("c1", {"name": "רונית"})
    assert cu.created == [] and cu.updated[0][0] == "R1" and "due_date" in cu.updated[0][1]


def test_no_tasks_list_means_no_task():
    import os

    os.environ.pop("CLICKUP_TASKS_LIST_ID", None)
    assert reminder_tasks.open_task("c1", {"name": "x"}) is None


# ------------------------------------------------------------ the daily job


def test_due_and_unsigned_sends_the_tasks_text_and_completes_it(monkeypatch, read_log):
    cu = _ClickUp(monkeypatch, task=_task(-0.1))
    _pending_client(monkeypatch)
    sent = _sent(monkeypatch)
    out = sign_reminders.run(dry_run=True)
    assert out["reminded"] == 1
    name, to, kw = sent[0]
    assert (name, to) == ("sign_reminder", "r@x.co")
    assert kw["body"] == "היי רונית\n\nטקסט שדרור ערך"  # Dror's edit, as it reads now
    assert kw["cta_url"].startswith("https://sign.example/dev")
    assert cu.statuses == ["complete"] and cu.comments[-1].startswith("✅ התזכורת נשלחה")


def test_not_due_yet_sends_nothing(monkeypatch):
    _ClickUp(monkeypatch, task=_task(2))  # Dror pushed the date out
    _pending_client(monkeypatch)
    sent = _sent(monkeypatch)
    assert sign_reminders.run(dry_run=True)["reminded"] == 0 and sent == []


@pytest.mark.parametrize("task", [None, "closed"])
def test_a_closed_or_deleted_task_cancels_the_follow_up(monkeypatch, read_log, task):
    _ClickUp(monkeypatch, task=None if task is None else _task(-1, closed=True))
    _pending_client(monkeypatch)
    sent = _sent(monkeypatch)
    assert sign_reminders.run(dry_run=True)["reminded"] == 0 and sent == []
    assert any(e["action"] == "reminder_cancelled" for e in read_log())


def test_an_empty_text_is_reported_on_the_task_not_sent_blank(monkeypatch):
    cu = _ClickUp(monkeypatch, task=_task(-1, text="   "))
    _pending_client(monkeypatch)
    sent = _sent(monkeypatch)
    sign_reminders.run(dry_run=True)
    assert sent == [] and cu.comments[-1].startswith("❌")


def test_a_contract_from_before_the_task_still_gets_the_fixed_text(monkeypatch):
    _pending_client(monkeypatch, task_id=None)
    signing._put_pending("signpending:c1", {"client_id": "c1", "issued_at": int(time.time() - 3 * 86400),
                                            "reminders_sent": 0})
    sent = _sent(monkeypatch)
    assert sign_reminders.run(dry_run=True)["reminded"] == 1
    assert "body" not in sent[0][2]


# ------------------------------------------------------------ signing


def test_signing_closes_the_open_task(monkeypatch):
    cu = _ClickUp(monkeypatch, task=_task(2))
    _pending_client(monkeypatch)
    reminder_tasks.close_on_signed("c1")
    assert cu.statuses == ["complete"] and "חתם" in cu.comments[-1]


def test_the_body_override_keeps_the_button_and_signature():
    from src.lib import email_templates

    mail = email_templates.render("sign_reminder", body="היי רונית\n\nכתבתי לבד {לא תבנית}",
                                  client_name="x", cta_url="https://sign.example/dev?t=1")
    assert "כתבתי לבד {לא תבנית}" in mail["text"]  # taken as is, braces and all
    assert "https://sign.example/dev?t=1" in mail["html"] and "לחתימה על ההסכם" in mail["html"]


def test_no_employee_ever_picks_up_a_follow_up_task(monkeypatch, tmp_path):
    from src import lambda_handler

    monkeypatch.setattr(ClickUpClient, "get_task", lambda s, tid: {"id": "R1", "name": "תזכורת חתימה: רונית",
                                                                   "custom_fields": []})
    out = lambda_handler.route({"event": "taskCreated", "task_id": "R1"}, dry_run=True, source="tasks")
    assert "follow-up" in out["ignored"]

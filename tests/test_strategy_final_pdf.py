"""Closing a strategy's review task files its final PDF in the client's column."""

import pytest

from src.automations import clickup_to_claude as bot
from src.automations import strategy_bot
from src.lib import client_files, review_tasks


def _task(status_type="closed", date_closed="1790000000000"):
    return {"id": "rt1", "name": f"{review_tasks.STRATEGY.prefix}מכללת אלפא",
            "status": {"status": "complete", "type": status_type}, "date_closed": date_closed,
            "custom_fields": [{"type": "list_relationship", "name": "לקוח", "value": [{"id": "c1"}]}]}


def _draft(version, doc, date):
    return {"id": f"cm{version}", "date": str(date),
            "comment_text": f"{bot.DRAFT_PREFIX} גרסה {version}\nhttps://docs.google.com/document/d/{doc}/edit"}


@pytest.fixture
def fake(monkeypatch):
    state = {"task": _task(), "attached": []}

    class Clickup:
        def __init__(self, dry_run=False):
            pass

        def get_task(self, tid):
            return state["task"]

    monkeypatch.setattr(strategy_bot, "ClickUpClient", Clickup)
    monkeypatch.setattr(bot, "threads", lambda clickup, tid: [
        {"root": _draft(1, "docA", 100), "replies": [{"comment_text": "תחדד את הקהל", "date": "150"},
                                                     _draft(2, "docB", 200)]}])
    monkeypatch.setattr(client_files, "attach_doc", lambda crm, cid, field, doc_id, **kw:
                        state["attached"].append((cid, field, doc_id, kw.get("tag"))) or True)
    return state


def test_closing_files_the_latest_version_once(fake):
    out = strategy_bot.final_pdf("rt1", dry_run=True)
    assert out["attached"] and fake["attached"] == [("c1", "strategy_pdf", "docB", "v2-final")]
    assert "skipped" in strategy_bot.final_pdf("rt1", dry_run=True), "the same closing is filed once"
    fake["task"] = _task(date_closed="1790000999999")  # reopened, edited, closed again
    strategy_bot.final_pdf("rt1", dry_run=True)
    assert len(fake["attached"]) == 2


def test_an_open_task_files_nothing(fake):
    fake["task"] = _task(status_type="custom")
    assert "skipped" in strategy_bot.final_pdf("rt1", dry_run=True) and not fake["attached"]


def test_a_status_change_only_ever_files_a_closed_strategy(monkeypatch):
    from src import lambda_handler
    from src.lib import tasks
    from src.lib.clients import clickup

    dispatched = []
    monkeypatch.setattr(tasks, "dispatch", lambda name, **kw: dispatched.append(name) or {"queued": True})
    monkeypatch.setattr(clickup.ClickUpClient, "get_task", lambda self, tid: _task())
    lambda_handler._route_claude_task({"task_id": "rt1"}, "taskStatusUpdated", "rt1", True)
    assert dispatched == ["strategy_final_pdf"]
    monkeypatch.setattr(clickup.ClickUpClient, "get_task",
                        lambda self, tid: {"id": "t9", "name": "3 מודעות לוובינר", "status": {"type": "closed"}})
    out = lambda_handler._route_claude_task({"task_id": "t9"}, "taskStatusUpdated", "t9", True)
    assert dispatched == ["strategy_final_pdf"] and "ignored" in out, "no agent runs on a status change"


def test_a_subfolder_falls_back_to_the_folder(monkeypatch):
    from src.lib import client_folder

    monkeypatch.setattr(client_folder, "ensure_subfolders", lambda g, fid, dry_run=False: {"דוחות קמפיין": {"id": "sub1"}})
    assert client_folder.subfolder("f1", "דוחות קמפיין") == "sub1"
    monkeypatch.setattr(client_folder, "ensure_subfolders",
                        lambda g, fid, dry_run=False: (_ for _ in ()).throw(RuntimeError("Drive down")))
    assert client_folder.subfolder("f1", "דוחות קמפיין") == "f1"


# --------------------------------------------------- the email to the client


@pytest.fixture
def delivery(fake, monkeypatch):
    """The fake board, plus replies, the client's card, and Gmail drafts."""
    from src.lib import agent_tools
    from src.lib.clients.crm import CrmClient

    state = {"replies": [], "drafts": [], "deleted": [], "email": "yossi@example.com"}

    def reply(self, root, text):
        state["replies"].append((root, text))

    strategy_bot.ClickUpClient.reply = reply
    monkeypatch.setattr(CrmClient, "get_client", lambda self, cid: {
        "id": cid, "name": "מכללת אלפא", "first_name": "דנה", "email": state["email"]})
    monkeypatch.setattr(agent_tools, "create_draft", lambda to, subject, body, **kw: (
        state["drafts"].append({"to": to, "subject": subject, "body": body, **kw})
        or {"id": "d1", "to": to, "subject": subject}))
    monkeypatch.setattr(agent_tools, "delete_draft", lambda did: state["deleted"].append(did))
    # the thread the versions live in needs ids for the lookup
    monkeypatch.setattr(bot, "threads", lambda clickup, tid: [
        {"root": {**_draft(1, "docA", 100), "id": "r1"}, "replies": [_draft(2, "docB", 200)]}])
    return state


def test_closing_prepares_the_email_to_the_client_and_sends_nothing(delivery):
    out = strategy_bot.final_pdf("rt1", dry_run=True)
    assert out["delivery"] == "drafted"
    [draft] = delivery["drafts"]
    assert draft["to"] == "yossi@example.com" and draft["attachments"][0][0].endswith(".pdf")
    assert "—" not in draft["body"] and "–" not in draft["body"]
    explain, card = [t for _, t in delivery["replies"]]
    assert "לא נשלחת עד שתענה שלח" in explain
    assert card.startswith(bot.CARD_PREFIX) and "[draft:d1]" in card
    assert all(root == "r1" for root, _ in delivery["replies"]), "in the version's own thread"


def test_a_client_without_email_gets_no_draft_and_dror_is_told(delivery):
    delivery["email"] = ""
    out = strategy_bot.final_pdf("rt1", dry_run=True)
    assert out["delivery"] == "no_email" and delivery["drafts"] == []
    assert "אין מייל בכרטיס הלקוח" in delivery["replies"][0][1]


def test_a_failing_draft_does_not_undo_the_filed_pdf(delivery, monkeypatch):
    from src.lib import agent_tools

    monkeypatch.setattr(agent_tools, "create_draft",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gmail.compose refused")))
    out = strategy_bot.final_pdf("rt1", dry_run=True)
    assert out["attached"] and out["delivery"] == "failed"

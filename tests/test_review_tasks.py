"""The strategy and the monthly report as review tasks on משימות."""

from __future__ import annotations

import email

import pytest

from src.automations import campaign_summary, clickup_to_claude as bot, strategy_bot
from src.lib import agent_tools, emails, review_tasks, workers
from src.lib.clients.clickup import ClickUpClient

CLIENTS_LIST = "L-clients"
TASKS_LIST = "L-tasks"

FIELDS = [  # עובד comes first on the real list: the client must not land in it
    {"id": "f-worker", "name": "עובד", "type": "list_relationship",
     "type_config": {"subcategory_id": "L-agents"}},
    {"id": "f-client", "name": "לקוח", "type": "list_relationship",
     "type_config": {"subcategory_id": CLIENTS_LIST}},
]


class _ClickUp:
    """One משימות list in memory: tasks, comments, threads."""

    def __init__(self, monkeypatch, tasks=()):
        self.tasks = {t["id"]: t for t in tasks}
        self.created, self.top, self.replies = [], [], {}
        self.attached, self.statuses = [], []
        me = self

        def comment(s, tid, text):
            made = {"id": f"c{len(me.top) + 1}", "comment_text": text, "date": str(len(me.top)),
                    "reply_count": 0, "task": tid}
            me.top.append(made)
            return made

        def reply(s, cid, text):
            me.replies.setdefault(cid, []).append({"id": f"r{cid}-{len(me.replies[cid])}",
                                                  "comment_text": text, "date": "9"})
            for c in me.top:
                if c["id"] == cid:
                    c["reply_count"] = len(me.replies[cid])
            return {}

        def create_task(s, list_id, name, *, description=None, custom_fields=None,
                        status=None, due_date=None):
            task = {"id": f"t{len(me.created) + 1}", "name": name, "description": description,
                    "status": {"status": "to do", "type": "open"}, "custom_fields": []}
            me.created.append({"list": list_id, "name": name, "custom_fields": custom_fields})
            me.tasks[task["id"]] = task
            return task

        monkeypatch.setattr(ClickUpClient, "list_tasks", lambda s, lid: list(me.tasks.values()))
        monkeypatch.setattr(ClickUpClient, "create_task", create_task)
        monkeypatch.setattr(ClickUpClient, "get_list_fields", lambda s, lid: FIELDS)
        monkeypatch.setattr(ClickUpClient, "comment", comment)
        monkeypatch.setattr(ClickUpClient, "reply", reply)
        monkeypatch.setattr(ClickUpClient, "list_comments", lambda s, tid: [c for c in me.top])
        monkeypatch.setattr(ClickUpClient, "list_replies", lambda s, cid: list(me.replies.get(cid, [])))
        monkeypatch.setattr(ClickUpClient, "attach", lambda s, tid, data, name, content_type="":
                            me.attached.append(name) or {})
        monkeypatch.setattr(ClickUpClient, "set_status", lambda s, tid, st: me.statuses.append(st) or {})
        monkeypatch.setattr(ClickUpClient, "get_task", lambda s, tid: me.tasks[tid])

    def all_replies(self):
        return [r["comment_text"] for rs in self.replies.values() for r in rs]


@pytest.fixture
def lists(monkeypatch):
    monkeypatch.setenv("CLICKUP_LIST_ID", CLIENTS_LIST)
    monkeypatch.setenv("CLICKUP_TASKS_LIST_ID", TASKS_LIST)


@pytest.fixture
def answered():
    from src.lib import questionnaire_store as store

    defn = store.get_definition(store.default_id())
    store.record_answer("42", "מכללת דוגמה", defn, {"business_name": "מכללת אלפא",
                                                    "goals": "להכפיל הרשמות"})


@pytest.fixture
def no_email_to_dror(monkeypatch):
    sent = []
    monkeypatch.setenv("DROR_EMAIL", "dror@example.com")
    monkeypatch.setattr(emails, "send_template", lambda name, to, **kw: sent.append(name) or {})
    return sent


def _linked(task, client_id="42"):
    task["custom_fields"] = [{**FIELDS[1], "value": [{"id": client_id}]}]
    return task


# ------------------------------------------------------------ the task itself


def test_the_client_is_linked_through_the_client_field_never_the_first(monkeypatch, lists):
    board = _ClickUp(monkeypatch)
    assert review_tasks.client_field(ClickUpClient(dry_run=True), TASKS_LIST) == "f-client"
    tid = review_tasks.open_task(ClickUpClient(dry_run=True), review_tasks.STRATEGY, "42",
                                 "אסטרטגיה: מכללת דוגמה", "brief")
    assert board.created[0]["custom_fields"] == [{"id": "f-client", "value": {"add": ["42"]}}]
    # its explanation is a bot comment, so the webhook never takes it for feedback
    assert board.top[0]["comment_text"].startswith(bot.BOT_PREFIXES)
    # a second run finds the open task instead of opening another
    again = review_tasks.open_task(ClickUpClient(dry_run=True), review_tasks.STRATEGY, "42",
                                   "אסטרטגיה: מכללת דוגמה", "brief")
    assert again == tid and len(board.created) == 1


def test_a_closed_task_is_not_reused(monkeypatch, lists):
    board = _ClickUp(monkeypatch, tasks=[{"id": "old", "name": "אסטרטגיה: X",
                                          "status": {"type": "closed"}}])
    tid = review_tasks.open_task(ClickUpClient(dry_run=True), review_tasks.STRATEGY, "42",
                                 "אסטרטגיה: X", "brief")
    assert tid != "old" and len(board.created) == 1


def test_the_signing_follow_up_links_the_client_field_too(monkeypatch, lists):
    from src.lib import reminder_tasks

    board = _ClickUp(monkeypatch)
    reminder_tasks.open_task("42", {"name": "מכללת דוגמה"}, dry_run=True)
    assert board.created[0]["custom_fields"][0]["id"] == "f-client"


# ------------------------------------------------------------ the strategy


def test_the_strategy_arrives_as_a_version_on_its_task(monkeypatch, lists, answered, no_email_to_dror):
    board = _ClickUp(monkeypatch)
    out = strategy_bot.run("42", dry_run=True)
    assert out["task_id"] and board.created[0]["name"] == "אסטרטגיה: מכללת דוגמה"
    root = board.top[-1]["comment_text"]
    assert root.startswith(f"{bot.DRAFT_PREFIX} גרסה 1 · {workers.STRATEGIST.name}")
    assert board.attached == ["claude-v1.pdf"]
    assert workers.STATUS_REVIEW in board.statuses
    assert no_email_to_dror == []  # the task is the notice, not an email


def test_the_strategy_falls_back_to_email_without_a_tasks_list(monkeypatch, answered, no_email_to_dror):
    monkeypatch.setenv("CLICKUP_TASKS_LIST_ID", "")
    out = strategy_bot.run("42", dry_run=True)
    assert out["task_id"] is None and no_email_to_dror == ["strategy_ready"]


def test_a_reply_on_the_strategy_task_is_revised_by_the_strategist(monkeypatch, lists):
    task = _linked({"id": "s1", "name": "אסטרטגיה: מכללת דוגמה", "description": "brief",
                    "status": {"type": "open"}})
    board = _ClickUp(monkeypatch, tasks=[task])
    board.top.append({"id": "c1", "comment_text": f"{bot.DRAFT_PREFIX} גרסה 1 · אסטרטג\nlink\n\nטקסט",
                      "reply_count": 1, "date": "1"})
    board.replies["c1"] = [{"id": "r1", "comment_text": "תחדד את הפרסונות", "date": "2"}]
    seen = {}

    def loop(ai, tools, messages, *, job=""):
        seen["job"] = job
        return "## אסטרטגיה מתוקנת"

    monkeypatch.setattr(bot, "_agent_loop", loop)
    saved = {}
    from src.lib import task_docs

    monkeypatch.setattr(task_docs, "save", lambda name, md, **kw: saved.update(kw) or
                        {"id": "D2", "url": "https://docs.google.com/document/d/D2/edit"})
    out = bot.run("s1", comment="תחדד את הפרסונות", comment_id="r1", dry_run=True)
    assert out["worker"] == workers.STRATEGIST.name and seen["job"] == workers.STRATEGIST.job
    assert saved["subfolder"] == "אסטרטגיה" and out["version"] == 2


# ------------------------------------------------------------ the report


def test_the_report_arrives_as_a_task_with_the_pdf_and_an_email_card(monkeypatch, lists,
                                                                      no_email_to_dror):
    board = _ClickUp(monkeypatch)
    out = campaign_summary.run("42", dry_run=True, month="2026-06")
    assert out["task_id"] and board.created[0]["name"] == "דוח קמפיינים: מכללת דוגמה · יוני 2026"
    root = board.top[-1]
    assert root["comment_text"].startswith(bot.DRAFT_PREFIX) and "[month:2026-06]" in root["comment_text"]
    assert board.attached == ["campaign-report-2026-06-v1.pdf"]  # ASCII: ClickUp refuses Hebrew
    card = board.replies[root["id"]][-1]["comment_text"]
    assert card.startswith(bot.CARD_PREFIX) and "client@example.com" in card and "[draft:" in card
    assert no_email_to_dror == []


def test_the_reports_card_is_sent_only_by_drors_send(monkeypatch, lists, no_email_to_dror):
    board = _ClickUp(monkeypatch)
    out = campaign_summary.run("42", dry_run=True, month="2026-06")
    board.tasks[out["task_id"]] = _linked(board.tasks[out["task_id"]])
    root = board.top[-1]["id"]
    board.replies[root].append({"id": "yes", "comment_text": "שלח", "date": "10",
                                "user": {"id": 0}})  # the dry-run ClickUp's "me"
    result = bot.run(out["task_id"], comment="שלח", comment_id="yes", dry_run=True)
    assert result["sent"]["to"] == "client@example.com"


def test_a_reply_on_the_report_rebuilds_it_in_the_thread(monkeypatch, lists, no_email_to_dror):
    board = _ClickUp(monkeypatch)
    out = campaign_summary.run("42", dry_run=True, month="2026-06")
    board.tasks[out["task_id"]] = _linked(board.tasks[out["task_id"]])
    root = board.top[-1]["id"]
    board.replies[root].append({"id": "fb", "comment_text": "פחות טכני", "date": "10"})
    prompts = []
    from src.lib.clients.anthropic_ai import AnthropicClient

    real = AnthropicClient.complete
    monkeypatch.setattr(AnthropicClient, "complete",
                        lambda self, prompt, **kw: prompts.append(prompt) or real(self, prompt, **kw))
    result = bot.run(out["task_id"], comment="פחות טכני", comment_id="fb", dry_run=True)
    assert result["month"] == "2026-06" and "פחות טכני" in prompts[-1]
    in_thread = [r["comment_text"] for r in board.replies[root]]
    assert any(t.startswith(f"{bot.DRAFT_PREFIX} גרסה 2") for t in in_thread)
    assert in_thread[-1].startswith(bot.CARD_PREFIX)  # a fresh draft for the new PDF
    assert len(board.top) == 2  # the explanation and the one thread: no new thread


def test_the_router_leaves_review_tasks_alone_on_creation(monkeypatch):
    from src import lambda_handler

    monkeypatch.setattr(ClickUpClient, "get_task", lambda s, tid: {
        "id": tid, "name": "דוח קמפיינים: X · יוני 2026", "custom_fields": []})
    out = lambda_handler._route_claude_task({}, "taskCreated", "t9", True)
    assert "review task" in out["ignored"]


def test_a_draft_can_carry_the_pdf():
    draft = agent_tools.create_draft("a@b.c", "דוח", "היי", attachments=[("דוח.pdf", b"%PDF-1")],
                                     dry_run=True)
    assert draft == {"id": "draft-mock", "to": "a@b.c", "subject": "דוח"}
    mime = agent_tools._branded_mime("a@b.c", "s", "b")
    mime.add_attachment(b"%PDF-1", maintype="application", subtype="pdf", filename="דוח.pdf")
    parsed = email.message_from_bytes(mime.as_bytes())
    assert any(p.get_content_type() == "application/pdf" for p in parsed.walk())

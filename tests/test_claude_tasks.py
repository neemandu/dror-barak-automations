"""The משימות bot: comments as instructions, the linked client, the loop, its Docs."""

from __future__ import annotations

import pytest

from src.automations import clickup_to_claude as bot
from src.lib import agent_tools
from src.lib.agent_tools import Toolbox
from src.lib.clients.clickup import ClickUpClient


# ------------------------------------------------------------ comments


@pytest.mark.parametrize("text,expected", [
    ("קלוד, קצר יותר", "קצר יותר"),
    ("Claude: more formal please", "more formal please"),
    ("  @claude   שוב  ", "שוב"),
    ("קלוד", ""),
    ("🤖 Claude:\n\nטיוטה", None),
    ("❌ Claude לא הצליח", None),
    ("claudette", None),
    ("תודה קלוד", None),  # addressed only at the start
    ("", None),
])
def test_which_comments_are_instructions(text, expected):
    assert bot.instruction_in(text) == expected


def test_the_linked_client_is_read_from_the_relationship_field():
    # The shape ClickUp returned for a linked task (checked live, 25.9).
    task = {"custom_fields": [
        {"name": "לקוח", "type": "list_relationship",
         "value": [{"id": "86eya3gqt", "name": "אור גרשון", "status": "לקוח פעיל"}]}]}
    assert bot.linked_client_id(task) == "86eya3gqt"
    assert bot.linked_client_id({"custom_fields": [{"type": "list_relationship", "value": None}]}) is None
    assert bot.linked_client_id({}) is None


def test_a_revision_sees_the_thread_as_a_conversation():
    comments = [
        {"comment_text": "🤖 Claude עובד על המשימה."},          # ack: left out
        {"comment_text": "🤖 Claude:\n\nגרסה 1"},               # its answer
        {"comment_text": "נראה טוב"},                           # people: left out
        {"comment_text": "קלוד, קצר יותר"},                     # the instruction
    ]
    msgs = bot.conversation("המשימה", comments, "קצר יותר")
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "המשימה"), ("assistant", "גרסה 1"), ("user", "קצר יותר")]


def test_the_instruction_is_added_when_the_comment_list_lags():
    msgs = bot.conversation("המשימה", [{"comment_text": "🤖 Claude:\n\nגרסה 1"}], "יותר רשמי")
    assert msgs[-1] == {"role": "user", "content": "יותר רשמי"}


# ------------------------------------------------------------ the run


def _task_with_client(monkeypatch):
    monkeypatch.setattr(ClickUpClient, "get_task", lambda self, tid: {
        "id": tid, "name": "3 מודעות לוובינר", "description": "קהל: הסבה מקצועית",
        "custom_fields": [{"type": "list_relationship", "value": [{"id": "42"}]}]})


def test_a_linked_client_is_in_the_prompt(monkeypatch, read_log):
    from src.lib.clients.anthropic_ai import AnthropicClient

    _task_with_client(monkeypatch)
    prompts = []
    real = AnthropicClient.create_message

    def spy(self, messages, **kw):
        prompts.append(messages[0]["content"])
        return real(self, messages, **kw)

    monkeypatch.setattr(AnthropicClient, "create_message", spy)
    out = bot.run("t1", dry_run=True)
    assert out["client"]  # the fixture client's name
    assert "הלקוח שהמשימה עבורו" in prompts[0] and out["client"] in prompts[0]
    assert "3 מודעות לוובינר" in prompts[0]


def test_a_revision_reads_the_comments(monkeypatch, read_log):
    _task_with_client(monkeypatch)
    listed = []
    monkeypatch.setattr(ClickUpClient, "list_comments",
                        lambda self, tid: listed.append(tid) or [{"comment_text": "🤖 Claude:\n\nv1"}])
    bot.run("t1", instruction="קצר יותר", dry_run=True)
    assert listed == ["t1"]
    assert any(e["action"] == "draft_revised" for e in read_log())


def test_web_research_is_offered_to_claude(monkeypatch):
    from src.lib.clients.anthropic_ai import AnthropicClient

    ai = AnthropicClient(dry_run=True)
    bot._agent_loop(ai, Toolbox(dry_run=True), "task")
    assert {"web_search", "web_fetch", "drive_search"} <= set(ai.calls[-1]["tools"])


class _ScriptedAI:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen = []

    def create_message(self, messages, **kw):
        self.seen.append(list(messages))
        return self.responses.pop(0)


def test_a_paused_web_turn_is_resumed_not_returned():
    paused = {"stop_reason": "pause_turn", "content": [
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "x"}}]}
    done = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "סיכום המתחרים"}]}
    ai = _ScriptedAI(paused, done)
    assert bot._agent_loop(ai, Toolbox(dry_run=True), "task") == "סיכום המתחרים"
    assert ai.seen[1][-1] == {"role": "assistant", "content": paused["content"]}


# ------------------------------------------------------------ its Docs


def _capture_doc(monkeypatch):
    from src.lib import pdf

    made = {}

    def fake_convert(data, content_type, name, parent):
        made.update(data=data, content_type=content_type, name=name, parent=parent)
        return {"id": "d1", "webViewLink": "https://docs.google.com/document/d/d1/edit"}

    monkeypatch.setattr(pdf, "file_to_google_doc", fake_convert)
    return made


def test_a_doc_is_branded_and_lands_in_the_clients_folder(monkeypatch):
    from src.lib import branded_doc, client_folder

    made = _capture_doc(monkeypatch)
    monkeypatch.setattr(client_folder, "ensure", lambda crm, client: {"id": "F1"})
    monkeypatch.setattr(client_folder, "ensure_subfolders",
                        lambda google, fid: {"אסטרטגיה": {"id": "F1-strategy"}})
    box = Toolbox(client={"id": "42", "name": "מכללת X"}, crm=object())
    text, is_error = box.run("drive_create_doc", {"name": "מודעות", "content": "# כותרת\n- אחת"})
    assert not is_error and "d1" in text
    assert made["parent"] == "F1-strategy"
    assert made["content_type"] == branded_doc.DOCX_TYPE and made["data"][:2] == b"PK"


def test_without_a_client_a_doc_goes_to_my_drive(monkeypatch):
    made = _capture_doc(monkeypatch)
    monkeypatch.delenv("DRIVE_DEFAULT_PARENT_ID", raising=False)
    Toolbox().run("drive_create_doc", {"name": "רעיונות", "content": "טקסט"})
    assert made["parent"] == "root"


def test_an_explicit_folder_wins(monkeypatch):
    made = _capture_doc(monkeypatch)
    Toolbox(client={"id": "42", "name": "X"}, crm=object()).run(
        "drive_create_doc", {"name": "n", "content": "c", "folder_id": "CHOSEN"})
    assert made["parent"] == "CHOSEN"


def test_the_bot_still_has_no_send_or_delete_tool():
    names = {d["name"] for d in agent_tools.DEFINITIONS}
    assert not any(w in n for n in names for w in ("send", "delete", "share", "trash"))

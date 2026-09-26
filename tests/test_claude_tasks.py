"""The משימות bot: threads as feedback, versions as Docs + PDFs, the linked client."""

from __future__ import annotations

import pytest

from src.automations import clickup_to_claude as bot
from src.lib import agent_tools, task_docs
from src.lib.agent_tools import Toolbox
from src.lib.clients.clickup import ClickUpClient

DOC = "https://docs.google.com/document/d/DOC1/edit"


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
def test_which_comments_are_addressed_to_claude(text, expected):
    assert bot.instruction_in(text) == expected


def test_the_linked_client_is_read_from_the_relationship_field():
    # The shape ClickUp returned for a linked task (checked live, 25.9).
    task = {"custom_fields": [
        {"name": "לקוח", "type": "list_relationship",
         "value": [{"id": "86eya3gqt", "name": "אור גרשון", "status": "לקוח פעיל"}]}]}
    assert bot.linked_client_id(task) == "86eya3gqt"
    assert bot.linked_client_id({"custom_fields": [{"type": "list_relationship", "value": None}]}) is None
    assert bot.linked_client_id({}) is None


# ------------------------------------------------------------ a fake ClickUp


class _Board:
    """Comments and threads on one task, as ClickUp's API returns them."""

    def __init__(self, monkeypatch, top=(), replies=None, task=None):
        self.top = list(top)
        self.replies = dict(replies or {})
        self.posted, self.replied, self.attached = [], [], []
        board = self
        monkeypatch.setattr(ClickUpClient, "list_comments", lambda s, tid: list(board.top))
        monkeypatch.setattr(ClickUpClient, "list_replies", lambda s, cid: list(board.replies.get(cid, [])))
        monkeypatch.setattr(ClickUpClient, "comment", lambda s, tid, text: board.posted.append(text) or {})
        monkeypatch.setattr(ClickUpClient, "reply", lambda s, cid, text: board.replied.append((cid, text)) or {})
        monkeypatch.setattr(ClickUpClient, "attach",
                            lambda s, tid, data, name, content_type="application/pdf":
                            board.attached.append(name) or {})
        monkeypatch.setattr(ClickUpClient, "get_task", lambda s, tid: task or {
            "id": tid, "name": "3 מודעות לוובינר", "description": "קהל: הסבה מקצועית"})


def _draft(cid, version, text="המודעות"):
    return {"id": cid, "comment_text": f"🤖 Claude: גרסה {version}\n{DOC}\n\n{text}",
            "reply_count": 0, "date": "1"}


def _person(cid, text):
    return {"id": cid, "comment_text": text, "date": "2"}


# ------------------------------------------------------------ versions


def test_a_new_task_gets_version_1_as_a_doc_a_pdf_and_a_top_level_comment(monkeypatch, read_log):
    board = _Board(monkeypatch)
    out = bot.run("t1", dry_run=True)
    assert out["version"] == 1 and not out["revised"]
    assert len(board.posted) == 1 and board.replied == []
    assert board.posted[0].startswith("🤖 Claude: גרסה 1 · כותב תוכן\nhttps://docs.google.com/document/d/")
    assert board.attached == ["claude-v1.pdf"]
    entry = next(e for e in read_log() if e["action"] == "draft_posted")
    assert entry["url"].startswith("https://docs.google.com/document/d/")


def test_a_reply_in_claudes_thread_is_feedback_answered_in_the_thread(monkeypatch, read_log):
    root = {**_draft("C1", 1), "reply_count": 1}
    board = _Board(monkeypatch, top=[root], replies={"C1": [_person("R1", "קצר יותר")]})
    out = bot.run("t1", comment="קצר יותר", comment_id="R1", dry_run=True)
    assert out["revised"] and out["version"] == 2
    assert board.posted == [] and board.replied[0][0] == "C1"
    assert board.replied[0][1].startswith("🤖 Claude: גרסה 2")
    assert board.attached == ["claude-v2.pdf"]
    assert any(e["action"] == "draft_revised" for e in read_log())


def test_an_ordinary_comment_is_ignored_and_costs_nothing(monkeypatch):
    from src.lib.clients.anthropic_ai import AnthropicClient

    board = _Board(monkeypatch, top=[_draft("C1", 1), _person("P1", "נראה טוב")])
    monkeypatch.setattr(AnthropicClient, "create_message", lambda *a, **k: pytest.fail("billed Opus"))
    out = bot.run("t1", comment="נראה טוב", comment_id="P1", dry_run=True)
    assert "ignored" in out and board.posted == board.replied == []


def test_a_top_level_klod_comment_revises_the_latest_thread(monkeypatch):
    board = _Board(monkeypatch, top=[_draft("C1", 1), _draft("C2", 2), _person("K", "קלוד, רשמי יותר")])
    out = bot.run("t1", comment="קלוד, רשמי יותר", comment_id="K", dry_run=True)
    assert out["revised"] and board.replied[0][0] == "C2" and out["version"] == 3


def test_a_bare_klod_starts_a_fresh_thread(monkeypatch):
    board = _Board(monkeypatch, top=[_draft("C1", 1)])
    out = bot.run("t1", comment="קלוד", comment_id="K", dry_run=True)
    assert not out["revised"] and out["version"] == 2 and len(board.posted) == 1


def test_versions_count_replies_in_threads(monkeypatch):
    root = {**_draft("C1", 1), "reply_count": 3}
    replies = {"C1": [_person("R1", "קצר"), _draft("R2", 2), _person("R3", "עוד")]}
    _Board(monkeypatch, top=[root], replies=replies)
    out = bot.run("t1", comment="עוד", comment_id="R3", dry_run=True)
    assert out["version"] == 3


def test_the_revision_sees_the_thread_as_a_conversation():
    thread = {"root": _draft("C1", 1, "גרסה ראשונה"),
              "replies": [_person("R1", "קצר יותר"), _draft("R2", 2, "גרסה שנייה"),
                          _person("R3", "קלוד, רשמי")]}
    msgs = bot.conversation("המשימה", thread, "רשמי", dry_run=True)
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[2]["content"] == "קצר יותר" and msgs[-1]["content"] == "רשמי"
    assert "גרסה שנייה" in msgs[3]["content"]


def test_a_linked_client_is_in_the_prompt(monkeypatch):
    from src.lib.clients.anthropic_ai import AnthropicClient

    _Board(monkeypatch, task={"id": "t1", "name": "3 מודעות", "description": "",
                              "custom_fields": [{"name": "לקוח", "type": "list_relationship",
                                                 "value": [{"id": "42"}]}]})
    prompts = []
    real = AnthropicClient.create_message
    monkeypatch.setattr(AnthropicClient, "create_message",
                        lambda self, m, **kw: prompts.append(m[0]["content"]) or real(self, m, **kw))
    out = bot.run("t1", dry_run=True)
    assert out["client"] and out["client"] in prompts[0] and "הלקוח שהמשימה עבורו" in prompts[0]


def test_a_failed_pdf_does_not_lose_the_answer(monkeypatch, read_log):
    board = _Board(monkeypatch)
    monkeypatch.setattr(ClickUpClient, "attach", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("413")))
    out = bot.run("t1", dry_run=True)
    assert out["attached"] is False and len(board.posted) == 1
    assert any(e["action"] == "pdf_attach_failed" for e in read_log())


def test_a_long_answer_shows_a_preview_and_the_doc():
    body = bot._comment_body(2, DOC, "# כותרת\n" + "מילה " * 2000)
    assert DOC in body and "[ההמשך במסמך]" in body and len(body) < 2000
    short = bot._comment_body(1, DOC, "# שלוש כותרות\n- **אחת**\n- שתיים")
    assert "#" not in short.split(DOC)[1] and "**" not in short


# ------------------------------------------------------------ the loop


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


def test_web_research_is_offered_and_doc_writing_is_not_a_tool():
    from src.lib.clients.anthropic_ai import AnthropicClient

    ai = AnthropicClient(dry_run=True)
    bot._agent_loop(ai, Toolbox(dry_run=True), "task")
    offered = set(ai.calls[-1]["tools"])
    assert {"web_search", "web_fetch", "drive_search", "gmail_create_draft"} <= offered
    assert "drive_create_doc" not in offered  # every answer is a Doc already


def test_the_bot_has_no_send_delete_or_share_tool():
    names = {d["name"] for d in agent_tools.DEFINITIONS}
    assert not any(w in n for n in names for w in ("send", "delete", "share", "trash"))


# ------------------------------------------------------------ where Docs go


def test_a_clients_docs_go_to_a_mishimot_folder_in_their_folder(monkeypatch):
    from src.lib import client_folder
    from src.lib.clients.google import GoogleClient

    made = []
    monkeypatch.setattr(client_folder, "ensure", lambda crm, client: {"id": "F1"})
    monkeypatch.setattr(GoogleClient, "list_folder", lambda self, pid: [])
    monkeypatch.setattr(GoogleClient, "create_folder",
                        lambda self, name, pid: made.append((name, pid)) or {"id": "F1-m"})
    assert task_docs.folder_for({"id": "42", "name": "X"}, crm=object()) == "F1-m"
    assert made == [("משימות", "F1")]


def test_an_existing_folder_is_reused(monkeypatch):
    from src.lib.clients.google import GoogleClient

    monkeypatch.delenv("DRIVE_DEFAULT_PARENT_ID", raising=False)
    monkeypatch.setattr(GoogleClient, "list_folder", lambda self, pid: [
        {"id": "M", "name": "משימות Claude", "mimeType": "application/vnd.google-apps.folder"}])
    monkeypatch.setattr(GoogleClient, "create_folder", lambda *a: pytest.fail("made a second one"))
    assert task_docs.folder_for(None, crm=None) == "M"


def test_the_doc_is_the_branded_docx(monkeypatch):
    from src.lib import branded_doc, pdf

    got = {}
    monkeypatch.setattr(task_docs, "folder_for", lambda client, crm, subfolder="": "F")
    monkeypatch.setattr(pdf, "file_to_google_doc",
                        lambda data, ct, name, parent: got.update(data=data, ct=ct, parent=parent) or {"id": "D"})
    out = task_docs.save("מודעות - גרסה 1", "# כותרת\n- אחת", client={"name": "X"}, crm=None)
    assert out["id"] == "D" and got["ct"] == branded_doc.DOCX_TYPE and got["data"][:2] == b"PK"


def test_a_doc_link_is_found_in_a_comment():
    assert task_docs.doc_id_in(f"🤖 Claude: גרסה 1\n{DOC}\n\nטקסט") == "DOC1"
    assert task_docs.doc_id_in("אין קישור") is None


def test_the_client_is_never_the_agent(monkeypatch):
    # Two relationship fields: עובד (-> סוכנים) and לקוח (-> לקוחות). With the
    # client empty, the agent must not be taken for the client.
    monkeypatch.setenv("CLICKUP_LIST_ID", "CLIENTS")
    agent = {"name": "🎤 עובד", "type": "list_relationship",
             "type_config": {"subcategory_id": "AGENTS"}, "value": [{"id": "A1"}]}
    client = {"name": "לקוח", "type": "list_relationship",
              "type_config": {"subcategory_id": "CLIENTS"}, "value": []}
    assert bot.linked_client_id({"custom_fields": [agent, client]}) is None
    client["value"] = [{"id": "C9"}]
    assert bot.linked_client_id({"custom_fields": [agent, client]}) == "C9"



def test_a_revision_reads_an_earlier_version_without_its_title_block():
    # With the title and "prepared for" line left in, the model copied them into
    # the next version (seen live 26.9), giving its Doc a double title.
    doc = ("בדיקה: מבנה וובינר - גרסה 1\n"
           "הוכן עבור אור גרשון   ·   26 בספטמבר 2026\n\n"
           "פתיחה\nתוכן")
    assert task_docs.without_title(doc) == "פתיחה\nתוכן"
    no_client = "רעיונות - גרסה 1\n26 בספטמבר 2026\n\nרעיון אחד"
    assert task_docs.without_title(no_client) == "רעיון אחד"
    plain = "טקסט בלי כותרת"
    assert task_docs.without_title(plain) == plain


def test_a_copied_title_is_stripped_however_many_times_it_was_copied():
    doc = ("משימה - גרסה 3\nהוכן עבור X   ·   26 בספטמבר 2026\n"
           "משימה - גרסה 2\nהוכן עבור X   ·   26 בספטמבר 2026\n\n"
           "התוכן עצמו")
    assert task_docs.without_title(doc) == "התוכן עצמו"


def test_a_new_version_is_saved_without_a_copied_title(monkeypatch):
    from src.lib.clients.anthropic_ai import AnthropicClient

    saved = []
    monkeypatch.setattr(task_docs, "save", lambda name, md, **kw: saved.append(md) or
                        {"id": "D", "url": "https://docs.google.com/document/d/D/edit"})
    monkeypatch.setattr(AnthropicClient, "create_message", lambda *a, **k: {
        "stop_reason": "end_turn", "content": [{"type": "text", "text":
            "3 מודעות - גרסה 2\nהוכן עבור X   ·   26 בספטמבר 2026\n\n# מודעה 1\nטקסט"}]})
    _Board(monkeypatch)
    bot.run("t1", dry_run=True)
    assert saved == ["# מודעה 1\nטקסט"]

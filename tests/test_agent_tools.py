"""The tools the משימות agent gets on Dror's Google account."""

import base64
import email

from src.lib import agent_tools
from src.lib.agent_tools import Toolbox


def test_only_the_declared_tools_run():
    text, is_error = Toolbox(dry_run=True).run("drive_delete", {"file_id": "x"})
    assert is_error and "Unknown tool" in text


def test_nothing_can_send_mail_or_delete():
    names = {d["name"] for d in agent_tools.DEFINITIONS}
    assert "gmail_create_draft" in names
    assert not any(word in n for n in names for word in ("send", "delete", "share", "move"))


def test_without_the_read_scope_the_agent_is_not_offered_mail_reading(monkeypatch):
    # Drafts need only gmail.compose; search and read need gmail.readonly, which the
    # domain may not have granted. The agent must not be handed tools that fail.
    monkeypatch.setattr(agent_tools.google_auth, "can_mint",
                        lambda scopes: scopes != agent_tools.google_auth.GMAIL_READ_SCOPES)
    box = Toolbox()
    names = {d["name"] for d in box.definitions}
    assert "gmail_create_draft" in names and "drive_search" in names
    assert not names & agent_tools.MAIL_READ_TOOLS
    assert "Unknown tool" in box.run("gmail_search", {"query": "x"})[0]

    monkeypatch.setattr(agent_tools.google_auth, "can_mint", lambda scopes: True)
    assert agent_tools.MAIL_READ_TOOLS <= {d["name"] for d in Toolbox().definitions}


def test_a_refused_scope_is_not_asked_for_again_for_a_few_minutes(monkeypatch):
    from src.lib import google_auth

    calls = []

    def refuse(scopes=None, **kw):
        calls.append(scopes)
        raise google_auth.GoogleAuthError("unauthorized_client")

    monkeypatch.setattr(google_auth, "access_token", refuse)
    monkeypatch.setitem(google_auth._cache, "denied", {})
    assert not google_auth.can_mint(google_auth.GMAIL_READ_SCOPES)
    assert not google_auth.can_mint(google_auth.GMAIL_READ_SCOPES)
    assert len(calls) == 1


def test_a_draft_goes_out_in_the_house_style(monkeypatch):
    sent = {}

    def fake_post(url, *, gmail=False, **kwargs):
        sent.update(kwargs["json"])

        class R:
            def json(self):
                return {"id": "d1"}
        return R()

    monkeypatch.setattr(Toolbox, "_post", staticmethod(fake_post))
    logged = []
    box = Toolbox(log=lambda *a: logged.append(a))
    text, is_error = box.run("gmail_create_draft", {"to": "a@b.c", "subject": "שלום — מדרור",
                                                    "body": "תודה — נדבר"})
    assert not is_error and "Not sent" in text
    msg = email.message_from_bytes(base64.urlsafe_b64decode(sent["message"]["raw"]))
    assert "—" not in str(email.header.make_header(email.header.decode_header(msg["Subject"])))
    plain = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    html = next(p for p in msg.walk() if p.get_content_type() == "text/html")
    assert "—" not in plain.get_payload(decode=True).decode()
    # the client gets Dror's branded card and signature, with its band image inline
    assert "cid:" in html.get_payload(decode=True).decode()
    assert any(p.get_content_type() == "image/png" for p in msg.walk())
    assert logged and logged[0][0] == "gmail_draft_created"


def test_the_sdk_call_carries_the_house_style(monkeypatch):
    from src.lib.clients.anthropic_ai import AnthropicClient
    from src.lib import text_style

    ai = AnthropicClient(dry_run=True)
    ai.create_message([{"role": "user", "content": "x"}], system="אתה עוזר")
    assert text_style.AI_STYLE in ai.calls[-1]["system"]

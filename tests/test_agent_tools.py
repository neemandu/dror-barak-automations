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

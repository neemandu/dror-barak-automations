"""The Claude client: what reaches Dror is never a silently cut-off reply, and
every call's token use is logged."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.lib import agent_tools
from src.lib.clients import anthropic_ai
from src.lib.clients.anthropic_ai import AnthropicClient


def _live_with(response):
    """A client whose SDK returns ``response``, without a network or a key."""
    ai = AnthropicClient(dry_run=True)
    ai.dry_run = False
    calls = []

    def create(**params):
        calls.append(params)
        return response

    ai._sdk = SimpleNamespace(messages=SimpleNamespace(create=create))
    return ai, calls


def _response(stop_reason, text="תשובה", **usage):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=usage.get("i", 10), output_tokens=usage.get("o", 5),
                              cache_read_input_tokens=0),
    )


def test_a_cut_off_reply_is_an_error_not_a_report():
    # campaign_summary.py records it happening: a truncated recommendation reached Dror.
    ai, _ = _live_with(_response("max_tokens", "המלצה 3: להעלות את"))
    with pytest.raises(RuntimeError, match="cut off"):
        ai.complete("x", max_tokens=100)


def test_a_complete_reply_comes_back():
    ai, calls = _live_with(_response("end_turn", "הכול בסדר"))
    assert ai.complete("x", thinking=True) == "הכול בסדר"
    assert calls[0]["thinking"] == {"type": "adaptive"}


def test_every_call_logs_its_token_use(monkeypatch):
    logged = []
    monkeypatch.setattr(anthropic_ai, "_log_usage", lambda model, resp: logged.append(resp.usage.input_tokens))
    ai, _ = _live_with(_response("end_turn", i=1234))
    ai.complete("x")
    assert logged == [1234]


def test_the_research_and_report_calls_think():
    # Opus 4.8 without thinking writes its reasoning into the reply.
    import inspect

    from src.automations import campaign_summary, social_prep

    assert "thinking=True" in inspect.getsource(social_prep.analyze_profiles)
    assert "thinking=True" in inspect.getsource(campaign_summary._analysis)


def test_the_draft_tool_says_the_signature_is_added():
    # The layout adds Dror's signature; a model that signs off too signs twice.
    draft = next(d for d in agent_tools.DEFINITIONS if d["name"] == "gmail_create_draft")
    assert "signature" in draft["input_schema"]["properties"]["body"]["description"]

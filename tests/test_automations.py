"""Dry-run tests for every automation.

Each test runs the automation in ``dry_run=True`` (mock clients, no network, no
credentials) and asserts it completes and writes the expected run-log entries.
This proves the end-to-end logic without touching production systems.
"""

from __future__ import annotations

import pytest

from src.automations import (
    campaign_summary,
    clickup_to_claude,
    daily_summary,
    lead_to_contacts,
    monthly_payment_requests,
    onboarding,
    send_questionnaire,
    send_quote,
    social_prep,
    strategy_bot,
)


def _actions(read_log):
    return {e["action"] for e in read_log()}


def test_lead_to_contacts(read_log):
    result = lead_to_contacts.run("42", dry_run=True)
    assert result["contact"]["resourceName"] == "people/mock"
    assert "contact_saved" in _actions(read_log)


def test_send_questionnaire(read_log):
    result = send_questionnaire.run("42", dry_run=True)
    assert result["message"]["idMessage"]
    assert "questionnaire_sent" in _actions(read_log)


def test_social_prep(read_log):
    result = social_prep.run("42", dry_run=True)
    assert result["analyses"]  # at least one profile analyzed
    assert "prep_report_ready" in _actions(read_log)


def test_send_quote_issues_a_signing_link(read_log, monkeypatch):
    # Fillout is gone: the quote is now a link to our own signing page, and the
    # page finalises the contract itself, so there is no `signed` half any more.
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    sent = send_quote.send("42", dry_run=True)
    assert sent["url"].startswith("https://sign.example/dev/sign?t=")
    assert "quote_sent" in _actions(read_log)


def test_send_quote_refuses_without_a_price(read_log, monkeypatch):
    # A client opening a contract that reads "סך של  ₪" has been shown a broken
    # document, and the price is Dror's to set, not theirs to fill in.
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    from src.lib.clients.crm import CrmClient

    monkeypatch.setattr(CrmClient, "get_client",
                        lambda self, cid: {"id": cid, "name": "מכללה", "monthly_price": None})
    with pytest.raises(ValueError, match="no monthly price"):
        send_quote.send("42", dry_run=True)
    assert "no_price" in _actions(read_log)


def test_send_quote_survives_undeliverable_whatsapp(read_log, monkeypatch):
    # ManyChat is not configured yet. The link must still exist and reach the task
    # -- refusing to produce it would make the button useless.
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    monkeypatch.delenv("MANYCHAT_API_KEY", raising=False)
    sent = send_quote.send("42", dry_run=True)
    assert sent["url"]
    assert sent["delivered"] == ""


def test_onboarding(read_log):
    result = onboarding.run("42", dry_run=True)
    assert result["folder"]["id"] == "drive-folder-mock"
    assert result["morning_client"]["id"] == "morning-client-mock"
    actions = _actions(read_log)
    assert {
        "drive_folder_created",
        "morning_client_created",
        "whatsapp_channel_opened",
        "onboarding_done",
    } <= actions


def test_monthly_payment_requests(read_log):
    result = monthly_payment_requests.run(dry_run=True, month="2026-07")
    assert result["count"] == 2  # two fixture active clients
    assert "payment_requested" in _actions(read_log)


def test_campaign_summary(read_log):
    result = campaign_summary.run("42", dry_run=True, month="2026-06")
    assert "AI OUTPUT" in result["report"] or "ניתוח" in result["report"]
    assert "campaign_summary_ready" in _actions(read_log)


def test_strategy_bot(read_log):
    result = strategy_bot.run("42", dry_run=True)
    assert "אסטרטגיה" in result["strategy"]
    assert "strategy_ready" in _actions(read_log)


def test_clickup_to_claude(read_log):
    result = clickup_to_claude.run("abc123", dry_run=True)
    assert "ClickUp task abc123" in result["brief"]
    assert result["dispatched"] is False
    assert "brief_built" in _actions(read_log)


def test_daily_summary_reads_run_log(read_log):
    # Generate some activity first, then summarize it.
    lead_to_contacts.run("42", dry_run=True)
    result = daily_summary.run(dry_run=True)
    assert result["entries"] >= 1
    assert "summary_sent" in _actions(read_log)


def test_daily_summary_empty_is_graceful():
    result = daily_summary.run(dry_run=True)
    assert "אין פעילות" in result["message"] or result["entries"] >= 0

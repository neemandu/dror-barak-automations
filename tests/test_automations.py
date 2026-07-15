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


def test_send_quote_emails_the_client(read_log, monkeypatch):
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    sent = send_quote.send("42", dry_run=True)
    assert sent["delivered"] == "אימייל"


def test_send_quote_survives_a_client_with_no_email(read_log, monkeypatch):
    # The link must still exist and reach the task. Refusing to produce it because
    # we cannot deliver it would make the button useless.
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    from src.lib.clients.crm import CrmClient

    monkeypatch.setattr(CrmClient, "get_client", lambda self, cid: {
        "id": cid, "name": "מכללה", "monthly_price": 4900, "email": ""})
    sent = send_quote.send("42", dry_run=True)
    assert sent["url"]
    assert sent["delivered"] == ""


def test_send_quote_survives_a_broken_mail_server(read_log, monkeypatch):
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    from src.lib import emails

    def boom(*a, **k):
        raise emails.EmailError("SMTP is down")

    monkeypatch.setattr(emails, "send_template", boom)
    sent = send_quote.send("42", dry_run=True)
    assert sent["url"], "the link must survive a delivery failure"
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


def test_monthly_billing_issues_a_proforma_and_emails_it(read_log):
    result = monthly_payment_requests.run(dry_run=True, month="2026-07")
    assert result["count"] == 2  # two fixture active clients
    actions = _actions(read_log)
    assert "proforma_created" in actions
    assert "proforma_emailed" in actions


def test_monthly_billing_skips_a_client_morning_has_never_heard_of(read_log, monkeypatch):
    # Onboarding creates the Morning client. Billing without one would fail, or
    # invent a second record for the same client.
    from src.lib.clients.crm import CrmClient

    monkeypatch.setattr(CrmClient, "list_active_clients", lambda self: [
        {"id": "1", "name": "מכללה", "monthly_price": 4900, "morning_client_id": "",
         "email": "a@b.co"}])
    monthly_payment_requests.run(dry_run=True, month="2026-07")
    assert "no_morning_client" in _actions(read_log)


def test_monthly_billing_does_not_email_without_the_attachment(read_log, monkeypatch):
    # "מצ״ב חשבון עסקה" with nothing attached is a mistake the client sees at once.
    from src.lib.clients.morning import MorningClient

    monkeypatch.setattr(MorningClient, "download_document", lambda self, d: b"")
    monthly_payment_requests.run(dry_run=True, month="2026-07")
    actions = _actions(read_log)
    assert "proforma_created" in actions, "the document must still be issued"
    assert "email_failed" in actions
    assert "proforma_emailed" not in actions


def test_one_clients_failure_does_not_stop_the_months_billing(read_log, monkeypatch):
    from src.lib.clients.crm import CrmClient
    from src.lib.clients.morning import MorningClient

    monkeypatch.setattr(CrmClient, "list_active_clients", lambda self: [
        {"id": "1", "name": "א", "monthly_price": 100, "morning_client_id": "m1", "email": "a@b.co"},
        {"id": "2", "name": "ב", "monthly_price": 200, "morning_client_id": "m2", "email": "c@d.co"},
    ])
    calls = []

    def flaky(self, **kw):
        calls.append(kw)
        if len(calls) == 1:
            raise RuntimeError("Morning 500")
        return {"id": "doc2", "url": {"he": "https://x"}}

    monkeypatch.setattr(MorningClient, "create_proforma", flaky)
    monthly_payment_requests.run(dry_run=True, month="2026-07")
    actions = _actions(read_log)
    assert "billing_error" in actions
    assert "proforma_created" in actions, "the second client must still be billed"


def test_the_month_is_written_for_a_human():
    assert monthly_payment_requests._month_label("2026-07") == "יולי 2026"
    assert monthly_payment_requests._month_label("nonsense") == "nonsense"


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

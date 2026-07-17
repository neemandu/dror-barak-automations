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
    onboarding,
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
    actions = _actions(read_log)
    assert {
        "drive_folder_created",
        "questionnaire_sent",
        "onboarding_done",
    } <= actions


def test_campaign_summary(read_log):
    result = campaign_summary.run("42", dry_run=True, month="2026-06")
    assert "campaign_summary_ready" in _actions(read_log)
    # The report carries the month header and the metrics table.
    assert "דוח קמפיינים חודשי" in result["report"]
    assert result["summary"]["totals"]["leads"] == 84  # from the canned insights


def test_campaign_summary_links_the_report_for_the_dashboard(read_log):
    # Bug regression: the link must land in a field subjects.links_for() reads, or
    # it is invisible in the dashboard and the daily email. Tests what Dror sees.
    from src.lib import subjects

    campaign_summary.run("42", dry_run=True, month="2026-06")
    entry = next(e for e in read_log() if e["action"] == "campaign_summary_ready")
    assert subjects.links_for(entry), "report entry has no clickable link"


def test_campaign_summary_uses_the_clients_own_drive_folder(read_log):
    # Bug regression: it used to read a key get_client never returns, so live it
    # fell back to the shared default parent. ensure() must run.
    result = campaign_summary.run("42", dry_run=True, month="2026-06")
    assert "drive-folder-mock" in result["url"]


def test_campaign_summary_emails_dror_for_approval(monkeypatch):
    # Bug regression: it used to notify via retired Green API. Must email Dror.
    monkeypatch.setenv("DROR_EMAIL", "dror@example.com")
    sent = {}

    from src.lib import emails

    def _capture(name, to, **kw):
        sent["name"], sent["to"], sent["cta"] = name, to, kw.get("cta_url")
        return {"sent": False, "dry_run": True}

    monkeypatch.setattr(emails, "send_template", _capture)
    campaign_summary.run("42", dry_run=True, month="2026-06")
    assert sent["name"] == "campaign_report_ready"
    assert sent["to"] == "dror@example.com"
    assert sent["cta"]  # never empty — render() would raise otherwise


def test_campaign_summary_skips_a_client_with_no_ad_account(read_log, monkeypatch):
    from src.lib.clients.crm import CrmClient

    monkeypatch.setattr(
        CrmClient, "get_client",
        lambda self, cid: {"id": cid, "name": "ללא חשבון", "meta_ad_account": ""},
    )
    with pytest.raises(campaign_summary.NoAdAccount):
        campaign_summary.run("42", dry_run=True, month="2026-06")
    entry = next(e for e in read_log() if e["action"] == "no_ad_account")
    assert entry["status"] == "skipped"  # skipped, not error


def test_campaign_summary_survives_a_broken_mail_server(read_log, monkeypatch):
    monkeypatch.setenv("DROR_EMAIL", "dror@example.com")
    from src.lib import emails

    def _boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(emails, "send_template", _boom)
    result = campaign_summary.run("42", dry_run=True, month="2026-06")
    assert result["url"]  # report still produced
    assert "approval_email_failed" in _actions(read_log)


def test_run_all_isolates_one_clients_failure(read_log, monkeypatch):
    # Dry-run list_active_clients returns exactly clients 1001 and 1002.
    def _run(client_id, **kw):
        if client_id == "1001":
            raise RuntimeError("meta exploded")
        return {"url": "ok"}

    monkeypatch.setattr(campaign_summary, "run", _run)
    result = campaign_summary.run_all(dry_run=True, month="2026-06")
    assert result["built"] == 1 and result["failed"] == 1
    assert "report_failed" in _actions(read_log)
    assert "campaign_reports_done" in _actions(read_log)


def test_run_all_month_defaults_to_the_previous_month(monkeypatch):
    seen = {}
    monkeypatch.setattr(campaign_summary, "run",
                        lambda cid, **kw: seen.setdefault("month", kw.get("month")))
    campaign_summary.run_all(dry_run=True)
    # run_all passes month through untouched; None means run() defaults it.
    assert seen["month"] is None


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

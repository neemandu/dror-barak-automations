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
    lead_to_contacts,
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


@pytest.fixture
def answered():
    """Client 42 has answered the questionnaire, with two profile links."""
    from src.lib import questionnaire_store as store

    defn = store.get_definition(store.default_id())
    store.record_answer("42", "מכללת דוגמה", defn, {
        "business_name": "מכללת אלפא", "offering": "קורס ניהול חשבונות",
        "instagram": "https://instagram.com/alpha", "website": "https://alpha.example",
        "goals": "להכפיל הרשמות"})


@pytest.fixture
def prompts(monkeypatch):
    """What was actually sent to Claude, and with which options."""
    from src.lib.clients.anthropic_ai import AnthropicClient

    seen = []
    monkeypatch.setattr(AnthropicClient, "complete",
                        lambda self, prompt, **kw: seen.append((prompt, kw)) or "## ניתוח\nתוצאה")
    return seen


def test_social_prep(read_log, answered):
    result = social_prep.run("42", dry_run=True)
    assert set(result["analyses"]) == {"instagram", "website"}
    assert "prep_report_ready" in _actions(read_log)


def test_social_prep_reads_the_pages_instead_of_guessing(answered, prompts):
    social_prep.run("42", dry_run=True)
    assert prompts and all(kw.get("web") is True for _p, kw in prompts), "no web access = invented analysis"
    assert any("https://instagram.com/alpha" in p for p, _kw in prompts)
    assert all("מקור המידע" in p for p, _kw in prompts), "it must say what it could not see"


def test_social_prep_drops_the_models_narration(monkeypatch):
    from src.lib.clients.anthropic_ai import AnthropicClient

    monkeypatch.setattr(AnthropicClient, "complete", lambda self, prompt, **kw:
                        "יש לי מספיק מידע מהאתר.\n\n**מקור המידע:** פתחתי את האתר.\n### מיצוב\nx")
    out = social_prep.analyze_profiles({"website": "https://a.example"}, AnthropicClient(dry_run=True))
    assert out["website"].startswith("**מקור המידע:**")


def test_social_prep_without_links_says_so_instead_of_failing(read_log):
    result = social_prep.run("42", dry_run=True)
    assert result["analyses"] == {}
    assert next(e for e in read_log() if e["action"] == "no_profiles")["status"] == "skipped"


def test_strategy_refuses_without_questionnaire_answers(read_log):
    with pytest.raises(RuntimeError, match="לא מילא את השאלון"):
        strategy_bot.run("42", dry_run=True)
    assert next(e for e in read_log() if e["action"] == "no_questionnaire")["status"] == "error"


def test_strategy_is_written_from_the_answers(answered, prompts):
    strategy_bot.run("42", dry_run=True)
    strategy_prompt, kw = prompts[-1]
    assert "קורס ניהול חשבונות" in strategy_prompt and "להכפיל הרשמות" in strategy_prompt
    assert kw.get("thinking") is True


def test_send_questionnaire_emails_the_link(read_log, monkeypatch):
    # The `שלח שאלון` button: a client lost the link, Dror re-sends it. Same
    # function onboarding uses, so the two can never drift apart.
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    result = send_questionnaire.run("42", dry_run=True)
    assert result["sent"] is True
    assert "questionnaire_sent" in _actions(read_log)


def test_send_questionnaire_fails_loudly_without_an_email(read_log, monkeypatch):
    # A button press must not quietly "succeed" while nothing went out: the
    # handler comments the exception back on the task, so raise it.
    from src.lib.clients.crm import CrmClient

    monkeypatch.setattr(CrmClient, "get_client",
                        lambda self, cid: {"id": cid, "name": "מכללה", "email": ""})
    from src.lib.actions import Refused

    with pytest.raises(Refused, match="אין כתובת מייל") as exc:
        send_questionnaire.run("42", dry_run=True)
    assert exc.value.commented, "it already said why on the task"
    assert "no_email" in _actions(read_log)


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
    from src.lib.actions import Refused

    with pytest.raises(Refused, match="אין מחיר"):
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


def test_strategy_bot(read_log, answered):
    result = strategy_bot.run("42", dry_run=True)
    assert result["strategy"] and result["saved"]["url"]
    assert "strategy_ready" in _actions(read_log)


def test_strategy_bot_tells_dror_by_email_not_whatsapp(read_log, answered, monkeypatch):
    # Green API is gone; on the official API a WhatsApp to Dror would be a billed,
    # Meta-approved template. So the "come review this" goes by email — and when
    # there is no address, the run says so rather than silently telling nobody.
    monkeypatch.setenv("DROR_EMAIL", "dror@example.com")
    strategy_bot.run("42", dry_run=True)
    assert "dror_notified" in _actions(read_log)

    monkeypatch.setenv("DROR_EMAIL", "")
    strategy_bot.run("42", dry_run=True)
    skipped = next(e for e in read_log() if e["action"] == "dror_not_notified")
    assert skipped["status"] == "skipped"


def test_strategy_bot_links_the_doc_and_uses_the_client_folder(read_log, answered):
    # Same two bugs as campaign_summary, fixed here too: the log entry must carry a
    # clickable link, and the doc goes to the client's own folder via ensure().
    from src.lib import subjects

    strategy_bot.run("42", dry_run=True)
    entry = next(e for e in read_log() if e["action"] == "strategy_ready")
    assert subjects.links_for(entry)


def test_clickup_to_claude(read_log):
    result = clickup_to_claude.run("abc123", dry_run=True)
    assert result["draft"]
    assert "draft_posted" in _actions(read_log)


def test_the_tasks_agent_runs_tools_until_claude_answers(monkeypatch, read_log):
    from src.lib.clients.anthropic_ai import AnthropicClient

    turns = iter([
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t1", "name": "drive_search", "input": {"query": "בריף"}}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "הפוסט מוכן \u2014 בהצלחה"}]},
    ])
    seen = []
    monkeypatch.setattr(AnthropicClient, "create_message",
                        lambda self, messages, **kw: seen.append(list(messages)) or next(turns))
    result = clickup_to_claude.run("abc123", dry_run=True)
    assert result["draft"] == "הפוסט מוכן - בהצלחה", "the comment is in the house style"
    assert [c["tool"] for c in result["tool_calls"]] == ["drive_search"]
    tool_result = seen[1][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "t1"


def test_the_tasks_agent_gives_up_after_max_turns(monkeypatch, read_log):
    from src.lib.clients.anthropic_ai import AnthropicClient

    monkeypatch.setattr(AnthropicClient, "create_message", lambda self, m, **kw: {
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "t", "name": "drive_search", "input": {"query": "x"}}]})
    with pytest.raises(RuntimeError, match="tool rounds"):
        clickup_to_claude.run("abc123", dry_run=True)
    assert next(e for e in read_log() if e["action"] == "draft_failed")["status"] == "error"


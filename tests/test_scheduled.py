"""The EventBridge entrypoints — what happens when a scheduled job cannot run.

A scheduled Lambda's return value is not somewhere Dror ever looks, so each
handler has to put its own failure into the run-log, or the job dies silently.
"""

from __future__ import annotations

from src import scheduled


def _entries(read_log, action):
    return [e for e in read_log() if e["action"] == action]


def test_daily_email_without_a_recipient_is_a_visible_skip(read_log, monkeypatch):
    monkeypatch.setenv("DROR_EMAIL", "")
    result = scheduled.daily_email_handler({}, None)
    assert result["sent"] is False
    assert _entries(read_log, "no_recipient")[0]["status"] == "skipped"


def test_daily_email_that_cannot_send_logs_the_failure(read_log, monkeypatch):
    # A recipient but no SMTP: the shared sender refuses with the App Password
    # hint. The handler must record that, not just return it to EventBridge.
    monkeypatch.setenv("DROR_EMAIL", "dror@example.com")
    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(key, "")
    result = scheduled.daily_email_handler({}, None)
    assert result["sent"] is False
    failure = _entries(read_log, "email_failed")[0]
    assert failure["status"] == "error"
    assert "App Password" in failure["detail"]


def test_reminders_run_both_chases_even_if_the_first_dies(read_log, monkeypatch):
    from src.automations import sign_reminders

    def boom(**kwargs):
        raise RuntimeError("ClickUp 500")

    monkeypatch.setattr(sign_reminders, "run", boom)
    result = scheduled.reminders_handler({}, None)
    assert "error" in result["signatures"]
    assert "reminded" in result["questionnaires"], "one chase failing must not silence the other"
    assert _entries(read_log, "signatures_job_failed")[0]["status"] == "error"

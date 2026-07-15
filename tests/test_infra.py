"""Unit tests for shared infrastructure: retry, templates, run-log, config."""

from __future__ import annotations

import random

import pytest

from src.lib import config, run_log, whatsapp_templates
from src.lib.retry import RetryableError, retry


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    @retry(attempts=4, sleep=lambda _: None, rng=random.Random(0))
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableError("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3  # failed twice, succeeded on the third


def test_retry_raises_after_exhausting_attempts():
    calls = {"n": 0}

    @retry(attempts=3, sleep=lambda _: None, rng=random.Random(0))
    def always_fails():
        calls["n"] += 1
        raise RetryableError("nope")

    with pytest.raises(RetryableError):
        always_fails()
    assert calls["n"] == 3


def test_retry_ignores_non_retryable_exceptions():
    @retry(attempts=3, sleep=lambda _: None, exceptions=(RetryableError,))
    def raises_value_error():
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        raises_value_error()


def test_template_render_and_errors():
    msg = whatsapp_templates.render(
        "questionnaire", first_name="אבי", questionnaire_url="http://x"
    )
    assert "אבי" in msg and "http://x" in msg

    with pytest.raises(whatsapp_templates.TemplateError):
        whatsapp_templates.render("does_not_exist")

    with pytest.raises(whatsapp_templates.TemplateError):
        whatsapp_templates.render("questionnaire")  # missing params


def test_run_log_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "log.jsonl"))
    run_log.record("t", "action_a", "ok", client_id="1", detail="x")
    run_log.record("t", "action_b", "error", client_id="2")
    entries = run_log.read_all()
    assert [e["action"] for e in entries] == ["action_a", "action_b"]
    assert entries[1]["status"] == "error"


def test_config_require_raises_when_missing(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_KEY", raising=False)
    with pytest.raises(config.ConfigError):
        config.require("SOME_UNSET_KEY")


def test_per_call_context_survives_into_the_log_line(capsys):
    """Regression: LoggerAdapter.process() used to overwrite kwargs["extra"].

    Every `extra=` at every call site was silently discarded, so production logs
    showed a rejection with no reason and an action with no client_id.
    """
    import json

    from src.lib.logging_setup import get_logger

    log = get_logger("test_ctx", "run123")
    log.warning("rejected", extra={"reason": "bad signature", "status": 401})
    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(line)

    assert record["reason"] == "bad signature"   # the call-site context
    assert record["status"] == 401
    assert record["automation"] == "test_ctx"    # the bound context
    assert record["run_id"] == "run123"


def test_reserved_context_names_do_not_crash_the_automation(capsys):
    """`message` is a LogRecord attribute; logging raises KeyError on collision.

    daily_summary really does log `message=...`. A log line must never be able to
    fail the work it describes, so collisions are renamed, not raised.
    """
    import json

    from src.lib.logging_setup import get_logger

    log = get_logger("test_reserved", "run1")
    log.info("summary_sent", extra={"message": "שלום", "filename": "x.pdf",
                                    "client_id": "42"})
    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert record["msg"] == "summary_sent"
    assert record["message_"] == "שלום"      # renamed, not lost
    assert record["filename_"] == "x.pdf"
    assert record["client_id"] == "42"       # untouched

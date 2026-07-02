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

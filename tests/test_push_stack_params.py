"""push_stack_params — the safe way to get a .env value onto the stack.

Only the parts that run without AWS: the name mapping and the dry-run plan.
The live path is a change set the tool reviews before executing.
"""

from __future__ import annotations

import pytest

from src.tools import push_stack_params as tool


def test_parameter_names_map_to_env_keys_by_letters_alone():
    keys = ["SMTP_HOST", "CLICKUP_API_TOKEN", "MANYCHAT_FLOW_1142673", "DRIVE_TEMPLATE_IDS", "PATH"]
    assert tool.env_key_for("SmtpHost", keys) == "SMTP_HOST"
    assert tool.env_key_for("ClickUpApiToken", keys) == "CLICKUP_API_TOKEN"
    assert tool.env_key_for("ManyChatFlow1142673", keys) == "MANYCHAT_FLOW_1142673"
    assert tool.env_key_for("DriveTemplateIds", keys) == "DRIVE_TEMPLATE_IDS"
    assert tool.env_key_for("NoSuchThing", keys) is None


def test_template_declares_the_parameters_the_guide_pushes():
    params = tool.template_parameters()
    for name in ("SmtpHost", "SmtpUser", "SmtpPassword", "DrorEmail", "SmooveWebhookToken",
                 "DriveTemplateIds", "SignBaseUrl", "MetaAccessToken"):
        assert name in params, f"{name} is not a stack parameter"


def test_dry_run_plans_from_env_without_touching_aws(monkeypatch, capsys):
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("DROR_EMAIL", "dror@example.com")
    assert tool.run(["SmtpHost", "DrorEmail"], dry_run=True) is True
    out = capsys.readouterr().out
    assert "SmtpHost" in out and "SMTP_HOST" in out
    assert "smtp.example" not in out, "values must never be printed"


def test_an_empty_value_is_refused_unless_asked_for(monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "")
    with pytest.raises(SystemExit, match="empty"):
        tool.run(["SmtpPassword"], dry_run=True)
    assert tool.run(["SmtpPassword"], dry_run=True, allow_empty=True) is True


def test_an_unknown_parameter_is_named_not_guessed():
    with pytest.raises(SystemExit, match="not a parameter"):
        tool.run(["SmtpHots"], dry_run=True)

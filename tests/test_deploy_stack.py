"""deploy_stack — parameters from an env file, without AWS."""

from __future__ import annotations

import pytest

from src.tools import deploy_stack as tool

PARAMS = {
    "Stage": {"Default": "dev"},
    "SmtpHost": {"Default": ""},
    "ClickUpApiToken": {},                 # required, no default
    "WebhookDryRun": {"Default": "1"},
}


def test_create_uses_env_then_defaults_and_names_what_is_missing():
    params, _ = tool.plan_parameters(PARAMS, {"CLICKUP_API_TOKEN": "pk_x", "STAGE": "test"}, existing=None)
    by = {p["ParameterKey"]: p for p in params}
    assert by["ClickUpApiToken"]["ParameterValue"] == "pk_x"
    assert by["Stage"]["ParameterValue"] == "test"
    assert by["SmtpHost"]["ParameterValue"] == "" and by["WebhookDryRun"]["ParameterValue"] == "1"
    with pytest.raises(SystemExit, match="ClickUpApiToken"):
        tool.plan_parameters(PARAMS, {}, existing=None)


def test_update_keeps_what_the_env_file_does_not_set():
    # The trap this pins: WEBHOOK_DRY_RUN absent from .env must NOT reset a live
    # stack to the template default "1" (or, worse, "0" on a test stack).
    params, lines = tool.plan_parameters(PARAMS, {"SMTP_HOST": "smtp.example", "WEBHOOK_DRY_RUN": ""},
                                         existing={"Stage", "SmtpHost", "ClickUpApiToken", "WebhookDryRun"})
    by = {p["ParameterKey"]: p for p in params}
    assert by["SmtpHost"] == {"ParameterKey": "SmtpHost", "ParameterValue": "smtp.example"}
    assert by["WebhookDryRun"] == {"ParameterKey": "WebhookDryRun", "UsePreviousValue": True}
    assert by["ClickUpApiToken"]["UsePreviousValue"] is True
    assert "smtp.example" not in "\n".join(lines), "values are never printed"


def test_env_file_grammar_matches_config_load_dotenv(tmp_path):
    f = tmp_path / ".env.test"
    f.write_text('# comment\nA=1\nB="two words"\nC=\n\nnot a pair\n', encoding="utf-8")
    assert tool.read_env_file(f) == {"A": "1", "B": "two words", "C": ""}


def test_the_node_driver_gets_its_execute_bit_back(tmp_path):
    node = tmp_path / "CampaignReportFunction" / "playwright" / "driver" / "node"
    node.parent.mkdir(parents=True); node.write_bytes(b"ELF"); node.chmod(0o644)
    fixed = tool.fix_executable_bits(tmp_path)
    assert fixed == [node] and node.stat().st_mode & 0o111
    assert tool.fix_executable_bits(tmp_path) == [], "idempotent"

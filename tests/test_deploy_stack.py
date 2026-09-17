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


def test_prune_drops_docs_and_tests_but_never_the_code(tmp_path):
    fn = tmp_path / "WebhookFunction"
    for rel in ("src/app.py", "templates/t.html", "docs/contract_source.txt", "tests/test_x.py",
                "infra/template.yaml", "PIL/Image.py", "README.md", ".env.example"):
        f = fn / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text("x" * 100)
    assert tool.prune_build(tmp_path) > 0
    left = sorted(str(f.relative_to(fn)) for f in fn.rglob("*") if f.is_file())
    assert left == [".env.example", "PIL/Image.py", "src/app.py", "templates/t.html"]
    assert tool.prune_build(tmp_path) == 0, "idempotent"

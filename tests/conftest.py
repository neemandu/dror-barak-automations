"""Shared test fixtures.

Points the run-log at a temp file per test so automations can write freely and
tests can assert on what was logged, and ensures no real ``.env`` leaks in.
"""

from __future__ import annotations

import os

import pytest


def _blank(monkeypatch, *keys: str) -> None:
    """Make a variable look unset for the whole test.

    Deleting it is not enough. Any code path that calls ``config.load_dotenv``
    — every handler and every CLI does — re-reads the developer's ``.env`` with
    ``os.environ.setdefault``, which quietly puts a deleted key back mid-test.
    That is how a `pytest` run ended up writing its dry-run entries into the
    production run-log table. An **empty** value survives ``setdefault``, and
    ``config.get`` already treats empty as unset.
    """
    for key in keys:
        monkeypatch.setenv(key, "")


@pytest.fixture(autouse=True)
def isolated_run_log(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "run_log.jsonl"))
    # RUN_LOG_TABLE wins over RUN_LOG_PATH, so a developer's .env pointing at the
    # real table would send every test's log line to DynamoDB — slow, billable,
    # and it pollutes the dashboard Dror is looking at.
    _blank(monkeypatch, "RUN_LOG_TABLE", "IDEMPOTENCY_TABLE")
    # Keep tests hermetic: no recipient side-channels unless a test sets them.
    _blank(monkeypatch, "DROR_WHATSAPP", "DRIVE_DEFAULT_PARENT_ID", "DRIVE_TEMPLATE_IDS")

    # Meta Ads creds: a real token in a developer's .env would make the campaign
    # client build live and reach Graph, so the "no credentials needed" dry-run
    # tests would pass or fail depending on whose machine ran them.
    _blank(monkeypatch, "META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_BASE_URL")

    # No Google credentials, and no reaching AWS. Once GOOGLE_SECRET_ARN existed
    # in a developer's .env, the suite started calling Secrets Manager for real:
    # slow, dependent on credentials, and it made the "missing credentials" tests
    # pass or fail depending on which test ran first.
    _blank(monkeypatch, "GOOGLE_SECRET_ARN", "GOOGLE_SERVICE_ACCOUNT_JSON",
           "GOOGLE_SERVICE_ACCOUNT_JSON_B64", "GOOGLE_SERVICE_ACCOUNT_FILE",
           "GOOGLE_IMPERSONATE_SUBJECT")

    # google_auth caches the key and token at module level, so one test's fetch
    # would otherwise satisfy the next test's assertion that there is nothing.
    from src.lib import google_auth

    google_auth.reset_cache()

    # Signing/questionnaire links: fake but present, so automations that build a
    # link in dry-run (onboarding emails the questionnaire) don't fail on absence.
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret-for-tests")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")

    # The contract refuses to render without the provider's details. Give the
    # tests obviously-fake ones rather than let them read the real .env: a suite
    # that passes only because a developer's .env happens to be filled in is a
    # suite that passes for the wrong reason, and fails in CI.
    for key, value in {
        "PROVIDER_NAME": "בודק בדיקות",
        "PROVIDER_BUSINESS_ID": "000000000",
        "PROVIDER_ADDRESS": "רחוב הבדיקה 1, עיר",
        "PROVIDER_PHONE": "0500000000",
        "PROVIDER_EMAIL": "test@example.com",
        "PROVIDER_BANK": "00",
        "PROVIDER_BANK_BRANCH": "000",
        "PROVIDER_BANK_ACCOUNT": "000000",
    }.items():
        monkeypatch.setenv(key, value)
    yield


@pytest.fixture
def read_log():
    from src.lib import run_log

    def _read():
        return run_log.read_all()

    return _read

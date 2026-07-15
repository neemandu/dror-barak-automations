"""Shared test fixtures.

Points the run-log at a temp file per test so automations can write freely and
tests can assert on what was logged, and ensures no real ``.env`` leaks in.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def isolated_run_log(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "run_log.jsonl"))
    # Keep tests hermetic: no recipient side-channels unless a test sets them.
    for key in ("DROR_WHATSAPP", "DRIVE_DEFAULT_PARENT_ID", "DRIVE_TEMPLATE_IDS"):
        monkeypatch.delenv(key, raising=False)

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

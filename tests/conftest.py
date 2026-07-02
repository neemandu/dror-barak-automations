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
    yield


@pytest.fixture
def read_log():
    from src.lib import run_log

    def _read():
        return run_log.read_all()

    return _read

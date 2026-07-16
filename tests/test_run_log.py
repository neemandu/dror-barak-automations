"""Tests for the run-log, both backends.

This is the only record of what the automations did, and the only source for the
dashboard and the daily email. The interesting cases are the ones where history
goes missing quietly: a write that fails, two entries in the same millisecond, a
type DynamoDB will not store.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.lib import run_log


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------- file backend


def test_entries_round_trip(read_log):
    run_log.record("onboarding", "drive_folder_created", client_id="c1",
                   detail="https://drive.google.com/x")
    entries = run_log.read_all()
    assert len(entries) == 1
    assert entries[0]["automation"] == "onboarding"
    assert entries[0]["client_id"] == "c1"


def test_read_since_filters_by_time():
    run_log.record("a", "x")
    assert run_log.read_since(_now() - timedelta(minutes=1))
    assert run_log.read_since(_now() + timedelta(minutes=1)) == []


def test_extra_context_is_kept():
    run_log.record("sign", "signed", client_id="c1", url="https://x", sha="abc123")
    entry = run_log.read_all()[0]
    assert entry["url"] == "https://x"
    assert entry["sha"] == "abc123"


def test_a_corrupt_line_does_not_break_the_dashboard(tmp_path, monkeypatch):
    # A half-written line must not take out the whole history.
    path = tmp_path / "log.jsonl"
    path.write_text('{"ts":"2026-07-16T10:00:00Z","automation":"a","action":"ok"}\n'
                    '{"ts":"broken\n', encoding="utf-8")
    monkeypatch.setenv("RUN_LOG_PATH", str(path))
    entries = run_log.read_since(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert len(entries) == 1


def test_logging_never_fails_the_work_it_describes(monkeypatch, capsys):
    # A lost log line is a gap in the dashboard. A raised exception here would be
    # a failed onboarding — a client with no Drive folder.
    class Broken:
        def write(self, entry):
            raise RuntimeError("disk full")

    monkeypatch.setattr(run_log, "_store", lambda: Broken())
    entry = run_log.record("onboarding", "drive_folder_created", client_id="c1")
    assert entry["action"] == "drive_folder_created"
    assert "run_log_write_failed" in capsys.readouterr().out


# ---------------------------------------------------------- dynamo backend


def test_dynamo_item_shape():
    store = object.__new__(run_log._DynamoStore)
    written = {}
    store.table = type("T", (), {"put_item": lambda self, Item: written.update(Item)})()

    store.write({"ts": "2026-07-16T10:30:00Z", "automation": "sign", "action": "signed",
                 "status": "ok", "client_id": "c1", "detail": None, "dry_run": False,
                 "amount": 4900.5})
    assert written["day"] == "2026-07-16"          # partitioned by day
    assert written["ts_id"].startswith("2026-07-16T10:30:00Z#")
    assert written["expires_at"] > 0               # TTL, so it cannot grow forever
    assert "detail" not in written                 # None is dropped, read back as empty
    from decimal import Decimal
    assert written["amount"] == Decimal("4900.5")  # DynamoDB has no float


def test_two_entries_in_the_same_millisecond_do_not_overwrite_each_other():
    store = object.__new__(run_log._DynamoStore)
    items = []
    store.table = type("T", (), {"put_item": lambda self, Item: items.append(Item)})()

    same = {"ts": "2026-07-16T10:30:00Z", "automation": "a", "action": "x"}
    store.write(dict(same))
    store.write(dict(same))
    assert items[0]["ts_id"] != items[1]["ts_id"], (
        "the timestamp alone as a sort key would silently lose one of them"
    )


def test_reading_back_drops_storage_keys_and_restores_types():
    from decimal import Decimal

    got = run_log._from_dynamo({
        "day": "2026-07-16", "ts_id": "x#1", "expires_at": Decimal("123"),
        "ts": "2026-07-16T10:30:00Z", "automation": "sign", "action": "signed",
        "amount": Decimal("4900"),
    })
    assert "day" not in got and "ts_id" not in got and "expires_at" not in got
    assert got["amount"] == 4900 and isinstance(got["amount"], int)
    # The readers index these directly; absent must be empty, not a KeyError.
    assert got["client_id"] is None
    assert got["detail"] is None
    assert got["dry_run"] is False


def test_the_days_queried_cover_the_whole_range():
    days = run_log._days_from(_now() - timedelta(days=3))
    assert len(days) == 4  # three days back, plus today
    assert days[-1] == _now().date().isoformat()


def test_a_single_day_is_one_query():
    assert len(run_log._days_from(_now())) == 1


# ------------------------------------------------------------- the readers


def test_the_dashboard_and_the_daily_email_read_the_same_entries(read_log):
    from src.lib import subjects

    run_log.record("monthly_payment_requests", "proforma_created", client_id="c1",
                   detail="4900₪")
    run_log.record("campaign_summary", "campaign_report_built", "error", client_id="c2",
                   detail="token expired")

    entries = run_log.read_all()
    assert subjects.counts(entries) == {"total": 2, "ok": 1, "error": 1,
                                        "skipped": 0, "dry_run": 0}
    assert [s.key for s, _ in subjects.group_by_subject(entries)] == ["morning", "meta"]
    assert len(subjects.failures(entries)) == 1

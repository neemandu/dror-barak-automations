"""Tests for the Taskey -> ClickUp migration tool (dry-run)."""

from __future__ import annotations

from pathlib import Path

from src.tools import migrate_taskey_to_clickup as mig

SAMPLE = str(Path(__file__).resolve().parents[1] / "examples" / "taskey_sample.csv")


def test_canonical_matching_hebrew_and_english():
    assert mig._canonical_for("מחיר חודשי") == "monthly_price"
    assert mig._canonical_for("Monthly Price") == "monthly_price"
    assert mig._canonical_for("סטטוס Morning") == "morning_status"
    assert mig._canonical_for("totally unknown column") is None


def test_numeric_coercion():
    assert mig._coerce("monthly_price", "3,500 ₪") == 3500.0
    assert mig._coerce("service_type", "ניהול קמפיינים") == "ניהול קמפיינים"
    assert mig._coerce("monthly_price", "") is None


def test_dry_run_migration_maps_and_creates(read_log):
    result = mig.run(SAMPLE, list_id="list-1", dry_run=True)
    assert result["total"] == 3
    assert result["created"] == 3
    # Header auto-detection found the key columns.
    assert result["mapping"]["monthly_price"] == "מחיר חודשי"
    assert result["mapping"]["name"] == "שם לקוח"

    actions = [e["action"] for e in read_log()]
    assert "mapping_resolved" in actions
    assert actions.count("task_created") == 3
    assert "migration_done" in actions


def test_limit_stops_early(read_log):
    result = mig.run(SAMPLE, list_id="list-1", dry_run=True, limit=1)
    assert result["created"] == 1
    assert "limit_reached" in [e["action"] for e in read_log()]

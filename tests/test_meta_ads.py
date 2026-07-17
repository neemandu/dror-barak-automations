"""Meta Ads client — id normalization and the dry-run contract."""

from __future__ import annotations

import pytest

from src.lib import campaign_metrics as cm
from src.lib.clients.meta_ads import MetaAdsClient, MetaError, normalize_account_id


def test_normalize_adds_the_act_prefix_to_bare_digits():
    assert normalize_account_id("1234567890") == "act_1234567890"


def test_normalize_passes_a_well_formed_act_id_through():
    assert normalize_account_id("act_1234567890") == "act_1234567890"
    assert normalize_account_id("  act_1234567890  ") == "act_1234567890"


def test_normalize_rejects_a_pasted_url():
    # A URL has other digit runs (business id, section id); guessing one would
    # query the wrong account. Must fail loudly, not pick a number.
    with pytest.raises(MetaError):
        normalize_account_id("https://adsmanager.facebook.com/adsmanager/act_123/campaigns")
    with pytest.raises(MetaError):
        normalize_account_id("")


def test_dry_run_insights_feed_summarize_cleanly():
    client = MetaAdsClient(dry_run=True)
    rows = client.insights("act_100000000000001", since="2026-06-01", until="2026-06-30")
    summary = cm.summarize(rows, currency=client.account("act_100000000000001")["currency"])
    # The canned payload: one live campaign (84 leads, not 168 — lead_grouped is
    # not summed) plus one paused at zero.
    assert summary["totals"]["leads"] == 84
    assert summary["totals"]["spend"] > 0
    assert summary["currency"] == "ILS"
    assert len(summary["campaigns"]) == 2


def test_dry_run_records_the_intended_call():
    client = MetaAdsClient(dry_run=True)
    client.insights("act_1", since="2026-06-01", until="2026-06-30")
    assert client.calls[-1]["method"] == "insights"
    assert client.calls[-1]["ad_account_id"] == "act_1"

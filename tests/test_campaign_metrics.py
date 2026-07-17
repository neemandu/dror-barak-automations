"""Metrics extraction — the three Meta traps, against literal payloads.

Pure functions, so these need no client and no mocking: a canned Graph row in,
an asserted number out. Each test pins one thing that is easy to get subtly wrong
and impossible to see in dry-run.
"""

from __future__ import annotations

from datetime import date

from src.lib import campaign_metrics as cm


def test_leads_come_from_the_actions_array_as_an_int():
    row = {"actions": [{"action_type": "lead", "value": "14"}]}
    assert cm.leads_in(row) == 14  # int, not the "14" string Graph sends


def test_lead_action_types_are_not_summed():
    # `lead` is the grouped total; the specific types are the SAME conversions.
    # Adding them would report 42 leads for 14 real ones.
    row = {
        "actions": [
            {"action_type": "lead", "value": "14"},
            {"action_type": "onsite_conversion.lead_grouped", "value": "14"},
            {"action_type": "offsite_conversion.fb_pixel_lead", "value": "14"},
        ]
    }
    assert cm.leads_in(row) == 14


def test_lead_falls_through_to_the_next_type_when_absent():
    row = {"actions": [{"action_type": "offsite_conversion.fb_pixel_lead", "value": "9"}]}
    assert cm.leads_in(row) == 9


def test_a_month_with_no_leads_has_no_actions_key():
    # Meta omits `actions` entirely rather than sending []. Must read as zero.
    assert cm.leads_in({"spend": "100"}) == 0
    assert cm.leads_in({"actions": []}) == 0


def test_non_lead_actions_do_not_count_as_leads():
    row = {"actions": [{"action_type": "link_click", "value": "120"}]}
    assert cm.leads_in(row) == 0


def test_totals_derive_ctr_from_sums_not_the_average_of_campaign_ctrs():
    rows = [
        # CTR 10% (100/1000)
        {"campaign_name": "A", "spend": "500", "impressions": "1000", "clicks": "100"},
        # CTR 1% (10/1000)
        {"campaign_name": "B", "spend": "500", "impressions": "1000", "clicks": "10"},
    ]
    totals = cm.summarize(rows)["totals"]
    # Σclicks/Σimpressions = 110/2000 = 5.5%, NOT the mean of 10% and 1% (5.5% here
    # only by coincidence of equal impressions — the point is it uses the sums).
    assert totals["clicks"] == 110
    assert totals["impressions"] == 2000
    assert abs(totals["ctr"] - 110 / 2000) < 1e-9


def test_zero_spend_month_divides_by_nothing():
    rows = [{"campaign_name": "paused", "spend": "0", "impressions": "0", "clicks": "0"}]
    summary = cm.summarize(rows)
    totals = summary["totals"]
    assert totals["spend"] == 0
    assert totals["leads"] == 0
    # Undefined, not zero and not a ZeroDivisionError.
    assert totals["ctr"] is None
    assert totals["cpc"] is None
    assert totals["cost_per_lead"] is None


def test_empty_month_summarizes_without_error():
    summary = cm.summarize([])
    assert summary["campaigns"] == []
    assert summary["totals"]["spend"] == 0
    assert summary["totals"]["cost_per_lead"] is None


def test_campaigns_are_sorted_by_spend_descending():
    rows = [
        {"campaign_name": "small", "spend": "100"},
        {"campaign_name": "big", "spend": "900"},
    ]
    names = [c["name"] for c in cm.summarize(rows)["campaigns"]]
    assert names == ["big", "small"]


def test_month_range_handles_february_and_31_day_months():
    assert cm.month_range("2026-02") == ("2026-02-01", "2026-02-28")
    assert cm.month_range("2024-02") == ("2024-02-01", "2024-02-29")  # leap year
    assert cm.month_range("2026-07") == ("2026-07-01", "2026-07-31")


def test_previous_month_wraps_the_year():
    assert cm.previous_month(date(2026, 7, 17)) == "2026-06"
    assert cm.previous_month(date(2026, 1, 3)) == "2025-12"


def test_month_label_and_currency_symbol():
    assert cm.month_label_he("2026-06") == "יוני 2026"
    assert cm.currency_symbol("ILS") == "₪"
    assert cm.currency_symbol("XYZ") == "XYZ"  # unknown code degrades to itself

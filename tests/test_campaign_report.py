"""The campaign report renderer — the report-vs-contract divergence and safety."""

from __future__ import annotations

import pytest

from src.lib import campaign_metrics as cm
from src.lib import campaign_report as cr

_CLIENT = {"name": "מכללת דוגמה"}
_ACCOUNT = {"name": "מכללת דוגמה", "currency": "ILS"}


def _render(rows, *, analysis="ניתוח.", recs="המלצה."):
    summary = cm.summarize(rows, currency="ILS")
    fields = cr.fields_from(_CLIENT, _ACCOUNT, summary, month="2026-06",
                            analysis=analysis, recommendations=recs)
    return cr.render(fields)


def test_renders_a_zero_spend_month_rather_than_refusing():
    # The whole reason this isn't contract.py: a paused month is a finding, not a
    # hole. It must render, and its undefined rates show as —.
    html = _render([{"campaign_name": "paused", "spend": "0",
                     "impressions": "0", "clicks": "0"}])
    assert "מכללת דוגמה" in html
    assert "—" in html  # cost-per-lead / CTR are undefined at zero spend


def test_renders_a_normal_month_with_real_numbers():
    html = _render([{"campaign_name": "וובינר", "spend": "5095", "impressions": "31066",
                     "clicks": "916", "actions": [{"action_type": "lead", "value": "84"}]}])
    assert "וובינר" in html
    assert "84" in html
    assert "₪" in html


def test_a_campaign_name_cannot_inject_markup():
    html = _render([{"campaign_name": "<script>alert(1)</script>", "spend": "10"}])
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_ai_prose_is_escaped_before_it_becomes_paragraphs():
    html = _render([{"campaign_name": "a", "spend": "1"}],
                   analysis="שורה עם <b> לא בטוח")
    assert "<b> לא בטוח" not in html
    assert "&lt;b&gt;" in html


def test_no_placeholder_survives_rendering():
    html = _render([{"campaign_name": "a", "spend": "1"}])
    assert "{{" not in html and "}}" not in html


def test_a_template_placeholder_with_no_code_key_is_an_error():
    with pytest.raises(cr.ReportError):
        cr.render({"client_name": "x"}, template="<p>{{client_name}} {{does_not_exist}}</p>")


def test_empty_ai_sections_render_as_a_dash_not_a_refusal():
    html = _render([{"campaign_name": "a", "spend": "1"}], analysis="", recs="")
    # Two AI sections, both empty → both —; the report still renders.
    assert html.count("—") >= 2

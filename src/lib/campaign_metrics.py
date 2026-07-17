"""Turning Meta's insights rows into the numbers Dror's report shows.

Pure functions, no I/O — :mod:`src.lib.clients.meta_ads` fetches the rows, this
module does the arithmetic, and :mod:`src.lib.campaign_report` lays them out. The
split is deliberate: extracting leads and totalling a month has three traps that
are far easier to test against a literal payload than against a live client.

**Leads are not a field.** They live inside each row's ``actions`` array, and the
same lead shows up under several ``action_type`` names — ``lead`` is usually the
sum of the more specific ones. Adding them together double-counts. So the rule is
a priority list and the *first* type present wins (see :data:`LEAD_ACTION_TYPES`).
A month with no leads has no ``actions`` key at all — Meta omits it rather than
send ``[]`` — so "no leads" reads as zero, not as an error.

**Totals are recomputed, never averaged.** Meta returns ``ctr``/``cpc`` per
campaign; the account CTR is ``Σclicks / Σimpressions``, not the mean of ten
campaigns' CTRs. So this module ignores Meta's derived fields and recomputes from
summed spend/impressions/clicks/leads — which also puts every divide-by-zero in
one place.

**Zero spend divides by nothing.** A paused month is a legitimate report, not a
failure. Rates come back as ``None`` (rendered ``—`` downstream), never ``0`` and
never ``inf``.

Numbers arrive from Graph as JSON *strings* (``"1147.55"``, ``"14"``); everything
here coerces through :func:`_num`.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any, Optional

# Priority order. The FIRST type present on a row is the lead count for that row;
# the rest are the same conversions counted a different way and must not be added.
# `lead` is Meta's own grouped total and is the most trustworthy when present.
LEAD_ACTION_TYPES = (
    "lead",
    "onsite_conversion.lead_grouped",
    "offsite_conversion.fb_pixel_lead",
    "leadgen.other",
)

CURRENCY_SYMBOLS = {"ILS": "₪", "USD": "$", "EUR": "€", "GBP": "£"}

_HE_MONTHS = [
    "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
    "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
]


def _num(value: Any) -> float:
    """Coerce a Graph value to a float. Graph sends numbers as strings."""
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    """Divide, or ``None`` when the denominator is zero.

    The zero guard for the whole module: a paused month has zero impressions and
    zero clicks, and its CTR/CPC/cost-per-lead are *undefined*, not zero.
    """
    if not denominator:
        return None
    return numerator / denominator


def month_range(month: str) -> tuple[str, str]:
    """``"2026-06"`` → ``("2026-06-01", "2026-06-30")`` — the calendar month.

    An explicit range, not Meta's ``date_preset``: ``last_month`` is relative to
    *today*, so it would report the wrong month whenever the job is re-run or
    ``--month`` is passed for a back-report.
    """
    year, mon = (int(part) for part in month.split("-"))
    last_day = calendar.monthrange(year, mon)[1]
    return f"{year:04d}-{mon:02d}-01", f"{year:04d}-{mon:02d}-{last_day:02d}"


def previous_month(today: Optional[date] = None) -> str:
    """The month before ``today`` (default: real today), as ``"YYYY-MM"``.

    The report's default period: on the 1st, the month that just closed. Wraps the
    year, so January reports December of the year before.
    """
    today = today or date.today()
    year, mon = today.year, today.month
    if mon == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{mon - 1:02d}"


def month_label_he(month: str) -> str:
    """``"2026-06"`` → ``"יוני 2026"`` for the report header."""
    year, mon = (int(part) for part in month.split("-"))
    return f"{_HE_MONTHS[mon - 1]} {year}"


def currency_symbol(code: str) -> str:
    """The symbol for a currency code, or the code itself if unknown."""
    return CURRENCY_SYMBOLS.get((code or "").upper(), code or "")


def leads_in(row: dict[str, Any]) -> int:
    """Leads for one insights row — first matching action type wins, never summed.

    See the module docstring: the lead-ish action types overlap, so adding them
    double-counts. A row with no ``actions`` key means no leads → 0.
    """
    actions = row.get("actions") or []
    by_type = {str(a.get("action_type") or ""): _num(a.get("value")) for a in actions}
    for action_type in LEAD_ACTION_TYPES:
        if action_type in by_type:
            return int(round(by_type[action_type]))
    return 0


def _campaign(row: dict[str, Any]) -> dict[str, Any]:
    """One row reduced to the numbers the report table shows."""
    spend = _num(row.get("spend"))
    impressions = int(_num(row.get("impressions")))
    clicks = int(_num(row.get("clicks")))
    leads = leads_in(row)
    return {
        "id": str(row.get("campaign_id") or ""),
        "name": str(row.get("campaign_name") or ""),
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "leads": leads,
        "ctr": _safe_div(clicks, impressions),          # fraction, e.g. 0.05
        "cpc": _safe_div(spend, clicks),
        "cost_per_lead": _safe_div(spend, leads),
    }


def _totals(campaigns: list[dict[str, Any]]) -> dict[str, Any]:
    """Account totals — rates recomputed from the sums, not averaged."""
    spend = sum(c["spend"] for c in campaigns)
    impressions = sum(c["impressions"] for c in campaigns)
    clicks = sum(c["clicks"] for c in campaigns)
    leads = sum(c["leads"] for c in campaigns)
    return {
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "leads": leads,
        "ctr": _safe_div(clicks, impressions),
        "cpc": _safe_div(spend, clicks),
        "cost_per_lead": _safe_div(spend, leads),
    }


def summarize(rows: list[dict[str, Any]], *, currency: str = "ILS") -> dict[str, Any]:
    """The whole month, ready for the report: totals + per-campaign + currency.

    Campaigns are sorted by spend, descending — the report leads with where the
    money went. Rows Meta returns with no delivery (``spend`` absent) still appear;
    a paused campaign at zero is a fact worth showing.
    """
    campaigns = sorted(
        (_campaign(row) for row in rows),
        key=lambda c: c["spend"],
        reverse=True,
    )
    return {
        "currency": currency,
        "symbol": currency_symbol(currency),
        "totals": _totals(campaigns),
        "campaigns": campaigns,
    }

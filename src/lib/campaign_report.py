"""Rendering the monthly campaign report from a month's metrics.

Mirrors :mod:`src.lib.contract`: a ``{{token}}`` template in ``templates/``,
comments stripped, filled in Python, converted to PDF by :mod:`src.lib.pdf` via
Drive (which handles Hebrew RTL properly). It reuses the contract's branding
assets — same ``templates/assets`` logo and footer.

**Where it deliberately parts from the contract.** ``contract.py`` refuses to
render on *any* empty value, because ``סך של  ₪`` is not a signable term. A report
is not a contract: a month with zero spend is a legitimate result — the campaign
was paused — and refusing to render it would hide exactly the month Dror needs to
see. So the rule splits:

  * a placeholder the code has **no key for** is still a hard error (a template
    that drifted from the code is a bug), and
  * a placeholder whose value is empty/``None`` renders as ``—``, not a refusal.

Campaign names come from Meta and are external data, so every non-RAW field is
HTML-escaped; only the pre-built markup fields (the table rows, the AI prose) are
inserted verbatim, the way the contract inserts a drawn signature.
"""

from __future__ import annotations

import base64
import html
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from . import campaign_charts, campaign_metrics, contract

EMPTY = "—"

# Brand-family bar colours, one hue per chart (dataviz: a single series needs no
# legend — the heading names it).
_SPEND_COLOR = (47, 125, 225)   # #2f7de1
_LEADS_COLOR = (10, 157, 140)   # #0a9d8c

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# Inserted as markup, not escaped: rows and prose we built ourselves, plus the
# branding data-URIs. Everything else is client/Meta data and gets escaped.
RAW_FIELDS = {
    "campaign_rows", "ai_summary", "ai_recommendations", "asset_logo", "asset_footer",
    "spend_chart", "leads_chart",
}


class ReportError(RuntimeError):
    """Raised when the report template and the code have drifted apart."""


def template_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "templates" / "campaign_report_he.html"
        if candidate.exists():
            return candidate
    raise ReportError("templates/campaign_report_he.html not found")


def load_template() -> str:
    return _COMMENT.sub("", template_path().read_text(encoding="utf-8"))


def placeholders_in(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


# ------------------------------------------------------------------ formatting

def _money(value: Any, symbol: str) -> str:
    """``5095.17`` → ``"5,095 ₪"``. Whole shekels: the report is a summary."""
    if value is None or value == "":
        return EMPTY
    return f"{int(round(float(value))):,} {symbol}".strip()


def _rate(value: Optional[float], symbol: str) -> str:
    """A per-unit cost (CPC, cost-per-lead) at two decimals, or ``—``."""
    if value is None:
        return EMPTY
    return f"{float(value):,.2f} {symbol}".strip()


def _pct(value: Optional[float]) -> str:
    """A fraction (``0.0295``) → ``"2.95%"``, or ``—``."""
    if value is None:
        return EMPTY
    return f"{float(value) * 100:.2f}%"


def _int(value: Any) -> str:
    if value is None or value == "":
        return EMPTY
    return f"{int(value):,}"


def _prose_to_html(text: str) -> str:
    """Plain AI text → escaped RTL paragraphs. Empty text → ``—``.

    Escape first, then add the ``<p>``/``<br>`` markup — so a model that returns
    an angle bracket can't inject anything into the report.
    """
    blocks = [b for b in (text or "").split("\n\n") if b.strip()]
    if not blocks:
        return EMPTY
    out = []
    for block in blocks:
        lines = [html.escape(line) for line in block.split("\n") if line.strip()]
        if lines:
            out.append("<p>" + "<br>".join(lines) + "</p>")
    return "".join(out) or EMPTY


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _charts(campaigns: list[dict[str, Any]], symbol: str) -> dict[str, str]:
    """Spend- and leads-by-campaign bar charts as embeddable data-URIs."""
    spend = campaign_charts.bar_chart(
        campaigns, value_key="spend", color=_SPEND_COLOR,
        fmt=lambda v: _money(v, symbol),
    )
    leads = campaign_charts.bar_chart(
        campaigns, value_key="leads", color=_LEADS_COLOR,
        fmt=lambda v: _int(v),
    )
    return {"spend_chart": _data_uri(spend), "leads_chart": _data_uri(leads)}


def _campaign_rows_html(campaigns: list[dict[str, Any]], symbol: str) -> str:
    """The per-campaign ``<tr>``s, each cell escaped as it is built."""
    if not campaigns:
        return '<tr><td colspan="7">לא נמצאה פעילות קמפיינים לחודש זה.</td></tr>'
    rows = []
    for c in campaigns:
        cells = [
            html.escape(str(c.get("name") or "")),
            _money(c.get("spend"), symbol),
            _int(c.get("impressions")),
            _int(c.get("clicks")),
            _pct(c.get("ctr")),
            _int(c.get("leads")),
            _rate(c.get("cost_per_lead"), symbol),
        ]
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return "".join(rows)


def fields_from(
    client: dict[str, Any],
    account: dict[str, Any],
    summary: dict[str, Any],
    *,
    month: str,
    analysis: str = "",
    recommendations: str = "",
    generated: Optional[str] = None,
) -> dict[str, str]:
    """Build every template value from a client, an account, and a month's metrics."""
    symbol = summary.get("symbol") or campaign_metrics.currency_symbol(
        account.get("currency") or ""
    )
    totals = summary.get("totals") or {}
    campaigns = summary.get("campaigns") or []
    return {
        **_charts(campaigns, symbol),
        "client_name": str(client.get("name") or ""),
        "account_name": str(account.get("name") or ""),
        "report_month": campaign_metrics.month_label_he(month),
        "generated_date": generated or date.today().strftime("%d/%m/%Y"),
        "total_spend": _money(totals.get("spend"), symbol),
        "total_impressions": _int(totals.get("impressions")),
        "total_clicks": _int(totals.get("clicks")),
        "total_ctr": _pct(totals.get("ctr")),
        "total_cpc": _rate(totals.get("cpc"), symbol),
        "total_leads": _int(totals.get("leads")),
        "total_cpl": _rate(totals.get("cost_per_lead"), symbol),
        # RAW fields — pre-built markup.
        "campaign_rows": _campaign_rows_html(summary.get("campaigns") or [], symbol),
        "ai_summary": _prose_to_html(analysis),
        "ai_recommendations": _prose_to_html(recommendations),
    }


def render(
    fields: dict[str, str],
    *,
    raw: Optional[dict[str, str]] = None,
    template: Optional[str] = None,
) -> str:
    """Fill the template. Errors on a placeholder the code doesn't know, ``—`` on blanks."""
    text = template if template is not None else load_template()
    raw = {**contract.assets(), **(raw or {})}
    combined = {**fields, **raw}

    # Structural check only: a placeholder with no key anywhere is a template/code
    # mismatch — a bug to catch at test time, not a value to paper over with —.
    needed = placeholders_in(text)
    unknown = sorted(n for n in needed if n not in combined and n not in RAW_FIELDS)
    if unknown:
        raise ReportError(
            "report template has placeholders the code does not fill: "
            + ", ".join(unknown)
        )

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in RAW_FIELDS:
            # RAW markup we built ourselves — the table rows and AI prose come from
            # `fields`, the branding data-URIs from `raw`; both go in verbatim.
            return str(combined.get(name, ""))
        value = fields.get(name)
        if value is None or str(value).strip() == "":
            return EMPTY
        return html.escape(str(value))

    out = _PLACEHOLDER.sub(substitute, text)

    leftover = placeholders_in(out)
    if leftover:  # belt and braces: nothing template-shaped may survive
        raise ReportError(f"placeholders survived rendering: {sorted(leftover)}")
    return out


_CSS = """
* { box-sizing: border-box; }
/* Rendered by headless Chromium (src/lib/pdf_chromium): real CSS, zero page
   margins, a full-bleed banner, crisp output. */
@page { size: A4; margin: 0; }
html, body { margin:0; padding:0; }
body { color:#14171a; font-family:'Segoe UI',system-ui,Arial,sans-serif;
  -webkit-print-color-adjust:exact; print-color-adjust:exact; }
/* Full-bleed gradient banner — edge to edge, no page margin. */
.brand-banner { margin:0; padding:30px 40px; text-align:center;
  background:linear-gradient(90deg,#00e5d0 0%,#00a8f0 45%,#2f7de1 100%); }
.brand-logo { width:230px; height:auto; display:block; margin:0 auto; }
/* Everything else is padded in from the page edges for readability. */
.content { padding:26px 40px 44px; }
h1 { font-size:24px; margin:0 0 6px; }
h2 { font-size:17px; margin:28px 0 12px; }
.lead { color:#14171a; font-size:16px; font-weight:600; margin:0 0 2px; }
.generated { color:#5b6472; font-size:13px; margin:0 0 8px; }
table.campaigns { width:100%; border-collapse:collapse; margin:6px 0; font-size:13px; }
table.campaigns th, table.campaigns td { border:1px solid #dfe3e8; padding:8px 10px;
  text-align:right; }
table.campaigns thead th { background:#f6f7f9; }
/* KPI cards — coloured tiles with white text. */
table.kpis { width:100%; border-collapse:separate; border-spacing:8px; }
table.kpis td.kpi { width:33%; padding:16px 14px; border-radius:12px;
  color:#ffffff; text-align:center; }
.kpi-num { font-size:23px; font-weight:700; }
.kpi-lbl { font-size:13px; opacity:.92; }
.chart { margin:6px 0 4px; }
.chart img { max-width:100%; height:auto; display:block; }
.ai p { font-size:14px; line-height:1.85; margin:0 0 10px; }
"""


def build_document(body: str, *, title: str = "דוח קמפיינים") -> str:
    """Wrap the rendered body in a self-contained HTML document for :mod:`pdf`.

    Carries its own ``<style>`` — unlike the contract, which is styled by the
    signing page. Drive's converter keeps table CSS; that is why the layout is
    tables and not flexbox.
    """
    return (
        "<!doctype html><html lang=\"he\" dir=\"rtl\"><head>"
        "<meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )

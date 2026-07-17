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

import html
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from . import campaign_metrics, contract

EMPTY = "—"

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# Inserted as markup, not escaped: rows and prose we built ourselves, plus the
# branding data-URIs. Everything else is client/Meta data and gets escaped.
RAW_FIELDS = {
    "campaign_rows", "ai_summary", "ai_recommendations", "asset_logo", "asset_footer",
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
    return {
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
body { margin:0; background:#eef0f3; color:#14171a;
  font-family:system-ui,"Segoe UI",Arial,sans-serif; }
.report { max-width:820px; margin:24px auto; background:#fff; padding:40px 48px;
  border-radius:10px; box-shadow:0 1px 4px rgba(0,0,0,.12); }
.brand-banner { border-collapse:collapse; margin:0 0 24px; }
/* The cell carries a solid bgcolor attribute for the Drive→Docs PDF (gradients
   don't survive that conversion); this gradient is only for the browser preview. */
.brand-cell { border-radius:8px; padding:26px 30px;
  background:linear-gradient(90deg,#00e5d0 0%,#00a8f0 45%,#2f7de1 100%); }
.brand-logo { width:240px; max-width:60%; height:auto; display:block; }
.brand-footer { margin-top:34px; text-align:center; }
.brand-footer img { max-width:100%; height:auto; }
h1 { font-size:24px; margin:0 0 6px; }
h2 { font-size:17px; margin:26px 0 10px; }
.lead { color:#14171a; font-size:16px; font-weight:600; margin:0 0 2px; }
.generated { color:#5b6472; font-size:13px; margin:0 0 8px; }
table { width:100%; border-collapse:collapse; margin:6px 0; font-size:14px; }
table.totals th { width:16%; text-align:right; background:#f6f7f9; padding:10px 12px;
  border:1px solid #dfe3e8; font-weight:600; }
table.totals td { width:18%; text-align:right; padding:10px 12px;
  border:1px solid #dfe3e8; font-weight:700; }
table.campaigns th, table.campaigns td { border:1px solid #dfe3e8; padding:8px 10px;
  text-align:right; }
table.campaigns thead th { background:#f6f7f9; font-size:13px; }
table.campaigns td { font-size:13px; }
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

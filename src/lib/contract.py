"""Rendering the contract from a ClickUp client record.

The template lives in ``templates/contract_he.html``, in the repo rather than in
Google Docs. A contract has to be provable: git history says exactly what the
terms were on any date, and the signed PDF freezes them at signing. A document
that can be edited freely after the fact can prove neither.

The central rule here: **an unfilled placeholder is an error, not a blank.** A
contract that reaches a client saying "סך של {{price_strategy}} ₪" — or worse,
an empty space where the price was — is not a document anyone should sign. So
rendering fails loudly rather than producing something plausible-looking.
"""

from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from . import config

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# A block shown only when its flag field is set: {{#has_campaigns}} ... {{/has_campaigns}}.
_SECTION = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.DOTALL)

# Dror's branding, lifted from his original document. Kept as real .png files
# rather than pasted into the template as base64: git can diff and store binaries
# sensibly, while 160KB of base64 in the middle of the contract would make every
# future change to the legal text unreadable.
#
# They are inlined as data URIs at render time because the document has to be
# self-contained — Drive converts it to PDF without fetching anything, and the
# signed PDF must not depend on a URL that could rot.
ASSET_FIELDS = {
    "asset_logo": "logo_header.png",
}

# Filled by the signing page, not from the CRM.
SIGNATURE_FIELDS = {"provider_signature", "client_signature"}

# Inserted as markup rather than escaped: these are ours, not client data.
RAW_FIELDS = SIGNATURE_FIELDS | set(ASSET_FIELDS)

# What the client must tell us before a contract can exist. These are what make it
# enforceable — you cannot sue a company you cannot identify.
REQUIRED_CLIENT_FIELDS = {
    "client_name": "שם הלקוח",
    "client_business_id": "ת.ז / ח.פ",
    "client_address": "כתובת",
    "client_phone": "טלפון",
    "client_email": "דוא״ל",
}

# Dror's own side of the contract — including his bank account. Kept in .env, not
# in the template: this repo is public, and a bank account number in it is a fraud
# vector that cannot be recalled once indexed.
PROVIDER_FIELDS = {
    "provider_name": "PROVIDER_NAME",
    "provider_business_id": "PROVIDER_BUSINESS_ID",
    "provider_address": "PROVIDER_ADDRESS",
    "provider_phone": "PROVIDER_PHONE",
    "provider_email": "PROVIDER_EMAIL",
    "provider_bank": "PROVIDER_BANK",
    "provider_bank_branch": "PROVIDER_BANK_BRANCH",
    "provider_bank_account": "PROVIDER_BANK_ACCOUNT",
}


def provider_fields() -> dict[str, str]:
    """Dror's details from the environment.

    Missing values are left empty on purpose rather than defaulted: ``render``
    refuses on a blank, so an unconfigured deployment fails loudly instead of
    sending a client a contract with no bank details to pay into.
    """
    return {key: str(config.get(env) or "") for key, env in PROVIDER_FIELDS.items()}


def missing_provider() -> list[str]:
    return [env for key, env in PROVIDER_FIELDS.items() if not config.get(env)]


class ContractError(RuntimeError):
    """Raised when a contract cannot be rendered correctly."""


def template_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "templates" / "contract_he.html"
        if candidate.exists():
            return candidate
    raise ContractError("templates/contract_he.html not found")


def load_template() -> str:
    """The template with HTML comments removed.

    Comments are notes to whoever maintains this, and they have no place in a
    document a client signs — they would travel into the rendered contract and its
    PDF. Stripping them also stops an example token written in a comment from
    being read as a real field.
    """
    return _COMMENT.sub("", template_path().read_text(encoding="utf-8"))


def placeholders_in(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


def _sections(text: str, fields: dict[str, str]) -> str:
    """Keep a ``{{#flag}}`` block when ``fields[flag]`` is set, drop it otherwise."""
    return _SECTION.sub(lambda m: m.group(2) if str(fields.get(m.group(1)) or "").strip() else "", text)


def assets() -> dict[str, str]:
    """Dror's branding as data URIs, so the document stands alone."""
    import base64

    root = template_path().parent / "assets"
    out: dict[str, str] = {}
    for field, filename in ASSET_FIELDS.items():
        path = root / filename
        if not path.exists():
            # Branding is cosmetic: a missing logo must not stop a contract going
            # out. The clause text is what matters.
            out[field] = ""
            continue
        encoded = base64.b64encode(path.read_bytes()).decode()
        out[field] = f"data:image/png;base64,{encoded}"
    return out


def _shekels(value: Any) -> str:
    """Format a price the way the contract writes them: 4,900."""
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ContractError(f"price {value!r} is not a number") from exc
    if number < 0:
        raise ContractError(f"price {value!r} is negative")
    return f"{int(round(number)):,}"


def _number(value: Any) -> float:
    try:
        return float(str(value if value not in (None, "") else 0).replace(",", "").strip())
    except ValueError as exc:
        raise ContractError(f"price {value!r} is not a number") from exc


def prices(client: dict[str, Any]) -> dict[str, Any]:
    """Which services the contract prices, and at what.

    ClickUp can hold a price per service (``מחיר אסטרטגיה`` / ``מחיר קמפיינים``).
    Without them there is only the one monthly price, and it goes to strategy,
    the service every client takes; ``split`` says which case this is, so the
    dashboard can say so before Dror sends it. A service with no price is not
    offered: its lines leave the contract rather than read "0 ₪".
    """
    strategy, campaigns = client.get("price_strategy"), client.get("price_campaigns")
    split = strategy not in (None, "") or campaigns not in (None, "")
    if not split:
        strategy, campaigns = client.get("monthly_price"), 0
    s, c = _number(strategy), _number(campaigns)
    return {"strategy": s, "campaigns": c, "total": s + c, "split": split,
            "has_strategy": s > 0 or c <= 0, "has_campaigns": c > 0}


def fields_from_client(
    client: dict[str, Any],
    *,
    price_strategy: Optional[Any] = None,
    price_campaigns: Optional[Any] = None,
    sign_date: Optional[str] = None,
) -> dict[str, str]:
    """Build the template's values from a CRM client record.

    ``price_strategy`` / ``price_campaigns`` override what the client record says
    (see :func:`prices`).
    """
    record = dict(client)
    if price_strategy is not None:
        record["price_strategy"] = price_strategy
    if price_campaigns is not None:
        record["price_campaigns"] = price_campaigns
    p = prices(record)
    both = p["has_strategy"] and p["has_campaigns"]
    return {
        **provider_fields(),
        "client_name": str(client.get("name") or ""),
        "client_business_id": str(client.get("business_id") or ""),
        "client_address": str(client.get("address") or ""),
        "client_phone": str(client.get("phone") or ""),
        "client_email": str(client.get("email") or ""),
        "sign_date": sign_date or date.today().strftime("%d / %m / %Y"),
        "price_strategy": _shekels(p["strategy"]),
        "price_campaigns": _shekels(p["campaigns"]),
        "price_total": _shekels(p["total"]),
        # Which price lines the contract shows, and their clause numbers.
        "has_strategy": "1" if p["has_strategy"] else "",
        "has_campaigns": "1" if p["has_campaigns"] else "",
        "has_both": "1" if both else "",
        "n_strategy": "10.1",
        "n_campaigns": "10.2" if both else "10.1",
        "n_total": "10.3",
        "n_payment": "10.4" if both else "10.2",
    }


def missing_for(fields: dict[str, str]) -> list[str]:
    """Required client details that are absent, by their Hebrew label."""
    return [
        label
        for key, label in REQUIRED_CLIENT_FIELDS.items()
        if not str(fields.get(key) or "").strip()
    ]


def render(
    fields: dict[str, str],
    *,
    signatures: Optional[dict[str, str]] = None,
    template: Optional[str] = None,
) -> str:
    """Fill the template. Raises rather than emit a contract with a hole in it.

    ``signatures`` carries already-safe markup (an <img> of a drawn signature) and
    is inserted verbatim; everything else is HTML-escaped, because a client name
    is data and must never be able to alter the contract's own text.
    """
    text = _sections(template if template is not None else load_template(), fields)
    signatures = signatures or {}
    raw = {**assets(), **signatures}

    needed = placeholders_in(text) - RAW_FIELDS
    absent = sorted(n for n in needed if not str(fields.get(n) or "").strip())
    if absent:
        raise ContractError(
            "refusing to render a contract with unfilled fields: "
            + ", ".join(absent)
            + ". A contract must never reach a client with a blank where a term "
              "should be."
        )

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in RAW_FIELDS:
            return raw.get(name, "")
        return html.escape(str(fields[name]))

    out = _PLACEHOLDER.sub(substitute, text)

    leftover = placeholders_in(out)
    if leftover:  # belt and braces: nothing template-shaped may survive
        raise ContractError(f"placeholders survived rendering: {sorted(leftover)}")
    return out


# ------------------------------------------------------------------ styles

# The contract's own look, shared by the signing page (screen) and the signed PDF
# (print), so the PDF is the document the client read, not a plainer cousin of it.
# Literal colours rather than the design system's tokens: the PDF has no tokens.
CONTRACT_CSS = """
.contract { color: #1d2939; }
.contract .brand-banner { border-radius: 12px; margin: 0 0 28px; padding: 26px 30px; display: flex; align-items: center;
  background: linear-gradient(100deg, #00e5d0 0%, #00a8f0 45%, #2f7de1 100%);
  -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.contract .brand-logo { max-width: 240px; height: auto; display: block; }
.contract h1 { font-size: 26px; font-weight: 800; letter-spacing: -.01em; margin: 0 0 6px; }
.contract h2 { font-size: 17px; font-weight: 700; margin: 30px 0 8px; }
.contract h3 { font-size: 15px; font-weight: 700; margin: 18px 0 6px; }
.contract p, .contract li { font-size: 14.5px; line-height: 1.85; }
.contract hr { border: 0; border-top: 1px solid #e5e8ed; margin: 26px 0; }
.contract .lead { color: #667085; font-size: 15.5px; }
.contract .note { color: #667085; font-size: 13px; }
.contract .filled { background: #fff4d6; padding: 1px 5px; border-radius: 5px; font-weight: 600; }
.contract bdi[dir=ltr] { unicode-bidi: isolate; }
.contract .parties { display: flex; gap: 32px; flex-wrap: wrap; }
.contract .party { flex: 1; min-width: 220px; }
.contract table.annex { width: 100%; border-collapse: separate; border-spacing: 0; margin: 14px 0; font-size: 13.5px;
  border: 1px solid #e5e8ed; border-radius: 10px; overflow: hidden; }
.contract table.annex th, .contract table.annex td { padding: 10px 12px; text-align: right; border-bottom: 1px solid #eef0f3; }
.contract table.annex th { background: #f9fafb; font-weight: 600; color: #344054; }
.contract table.annex tr:last-child td { border-bottom: 0; }
.contract table.annex .total { font-weight: 700; background: #f9fafb; }
.contract table.annex .num { white-space: nowrap; }
.contract .signatures { display: flex; gap: 32px; flex-wrap: wrap; }
.contract .sig { flex: 1; min-width: 240px; }
.contract .sig-box { display: flex; align-items: flex-end; border-bottom: 1.5px solid #1d2939; height: 74px; margin: 6px 0; }
.contract .sig-box img { display: block; max-height: 70px; max-width: 100%; }
"""

# Print only: A4 with room for the footer, the text a touch denser than on screen,
# and nothing split that reads badly across a page break.
_PRINT_CSS = """
@page { size: A4; margin: 15mm 16mm 19mm;
  @bottom-center { content: "{{footer}}   |   עמוד " counter(page) " מתוך " counter(pages);
    direction: rtl; font-family: Heebo, 'DejaVu Sans', Arial, sans-serif; font-size: 8pt; color: #667085;
    border-top: 0.5pt solid #e5e8ed; padding-top: 2.5mm; } }
html, body { margin: 0; padding: 0; background: #fff; }
body { font-family: Heebo, 'DejaVu Sans', Arial, sans-serif; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.contract p, .contract li { font-size: 10.5pt; line-height: 1.7; }
.contract h1 { font-size: 20pt; }
.contract h2 { font-size: 13pt; margin: 20px 0 6px; break-after: avoid-page; }
.contract h3 { font-size: 11pt; margin: 12px 0 4px; break-after: avoid-page; }
.contract hr { margin: 18px 0; }
.contract table.annex { font-size: 9.5pt; }
.contract p, .contract li, .contract table.annex tr, .contract .signatures, .contract .parties { break-inside: avoid; }
.contract .agreement-start { break-before: page; }
.contract hr:has(+ .agreement-start) { display: none; }
.audit { break-inside: avoid; margin-top: 26px; padding: 14px 18px; border: 1px solid #e5e8ed; border-radius: 10px;
  background: #f9fafb; color: #344054; font-size: 8.5pt; line-height: 1.6; }
.audit h2 { margin: 0 0 8px; font-size: 10.5pt; color: #1d2939; }
.audit table { border-collapse: collapse; width: 100%; }
.audit th { text-align: right; font-weight: 600; padding: 2px 0 2px 14px; white-space: nowrap; vertical-align: top; width: 1%; }
.audit td { padding: 2px 0; }
.audit code { font-family: 'DejaVu Sans Mono', Menlo, monospace; font-size: 7.5pt; word-break: break-all; }
.audit p { margin: 8px 0 0; color: #667085; }
"""


def _font_face() -> str:
    """Heebo, embedded: the signing page's typeface, in a PDF that fetches nothing."""
    import base64

    path = template_path().parent / "assets" / "fonts" / "Heebo-Variable.ttf"
    if not path.exists():
        return ""
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return (f"@font-face {{ font-family: 'Heebo'; font-weight: 100 900; "
            f"src: url(data:font/ttf;base64,{data}) format('truetype'); }}")


def print_document(body: str, audit_html: str, *, client_name: str, fingerprint: str) -> str:
    """The signed contract as a standalone page for the PDF printer.

    ``body`` is exactly what the client saw and signed (its hash is the
    fingerprint); the audit block follows it, and every page's footer carries
    the client and the fingerprint's start, so a page cannot be swapped in from
    another document unnoticed.
    """
    footer = "   |   ".join(x for x in (
        config.get("PROVIDER_NAME") or "דרור ברק", f"הסכם התקשרות עם {client_name}",
        f"טביעת אצבע {fingerprint[:12]}") if x)
    footer = footer.replace("\\", "\\\\").replace('"', '\\"')
    return (
        '<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">'
        f"<title>{html.escape(f'הסכם התקשרות - {client_name}')}</title>"
        f"<style>{_font_face()}{CONTRACT_CSS}{_PRINT_CSS.replace('{{footer}}', footer)}</style>"
        f"</head><body>{body}{audit_html}</body></html>"
    )

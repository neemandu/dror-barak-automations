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

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# Filled by the signing page, not from the CRM.
SIGNATURE_FIELDS = {"provider_signature", "client_signature"}

# What the client must tell us before a contract can exist. These are what make it
# enforceable — you cannot sue a company you cannot identify.
REQUIRED_CLIENT_FIELDS = {
    "client_name": "שם הלקוח",
    "client_business_id": "ת.ז / ח.פ",
    "client_address": "כתובת",
    "client_phone": "טלפון",
    "client_email": "דוא״ל",
}


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


def _shekels(value: Any) -> str:
    """Format a price the way the contract writes them: 4,900."""
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ContractError(f"price {value!r} is not a number") from exc
    if number < 0:
        raise ContractError(f"price {value!r} is negative")
    return f"{int(round(number)):,}"


def fields_from_client(
    client: dict[str, Any],
    *,
    price_strategy: Optional[Any] = None,
    price_campaigns: Optional[Any] = None,
    sign_date: Optional[str] = None,
) -> dict[str, str]:
    """Build the template's values from a CRM client record.

    ``price_strategy`` / ``price_campaigns`` override the CRM's single
    ``monthly_price``. The contract bills two line items separately, and ClickUp
    currently holds one number — see docs/CLICKUP_SETUP.md.
    """
    strategy = price_strategy if price_strategy is not None else client.get("price_strategy")
    campaigns = price_campaigns if price_campaigns is not None else client.get("price_campaigns")

    if strategy is None and campaigns is None:
        # Fall back to the single CRM price rather than invent a split. Which line
        # it belongs to is a real question, so it goes to strategy and campaigns
        # reads zero — visible and wrong-looking rather than silently halved.
        strategy = client.get("monthly_price")
        campaigns = 0

    strategy = strategy if strategy is not None else 0
    campaigns = campaigns if campaigns is not None else 0

    try:
        total = float(str(strategy).replace(",", "")) + float(str(campaigns).replace(",", ""))
    except ValueError as exc:
        raise ContractError(f"cannot total {strategy!r} + {campaigns!r}") from exc

    return {
        "client_name": str(client.get("name") or ""),
        "client_business_id": str(client.get("business_id") or ""),
        "client_address": str(client.get("address") or ""),
        "client_phone": str(client.get("phone") or ""),
        "client_email": str(client.get("email") or ""),
        "sign_date": sign_date or date.today().strftime("%d / %m / %Y"),
        "price_strategy": _shekels(strategy),
        "price_campaigns": _shekels(campaigns),
        "price_total": _shekels(total),
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
    text = template if template is not None else load_template()
    signatures = signatures or {}

    needed = placeholders_in(text) - SIGNATURE_FIELDS
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
        if name in SIGNATURE_FIELDS:
            return signatures.get(name, "")
        return html.escape(str(fields[name]))

    out = _PLACEHOLDER.sub(substitute, text)

    leftover = placeholders_in(out)
    if leftover:  # belt and braces: nothing template-shaped may survive
        raise ContractError(f"placeholders survived rendering: {sorted(leftover)}")
    return out

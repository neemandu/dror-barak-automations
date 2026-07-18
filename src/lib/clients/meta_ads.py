"""Meta Ads client — the month's campaign numbers for the report (T7).

Transport only: this fetches insights rows and account metadata from the Graph
API and hands them, untouched, to :mod:`src.lib.campaign_metrics` for the
arithmetic. Keeping the maths out of here is what lets the tricky parts (lead
extraction, zero-spend totals) be tested against literal payloads with no client.

Auth is a **system-user token** with ``ads_read`` (see docs/CREDENTIALS.md §5),
long-lived and static — so, unlike Google, it is read once in ``__init__`` rather
than minted per call. The token is a query parameter Graph requires; it is never
put in ``detail=`` or logged.

Two calls make a report:

  * :meth:`insights` — ``GET /{act_id}/insights`` at campaign level for one month.
  * :meth:`account` — the account's name (for the header) and currency. It earns
    its place on a **zero-spend month**, where insights returns ``data: []`` and
    so carries no ``account_currency`` to read the symbol from.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .. import config
from .base import BaseClient

DEFAULT_BASE_URL = "https://graph.facebook.com/v21.0"

# A cursor-paged account with hundreds of campaigns should still terminate; 20
# pages of 100 is 2000 campaigns, far past any real account, and a broken
# `paging.next` that never clears cannot spin forever.
MAX_PAGES = 20

_ACT_ID = re.compile(r"^act_\d+$")
_DIGITS = re.compile(r"\d{6,}")


class MetaError(RuntimeError):
    """A Meta Graph API call failed, or its input was malformed."""


def normalize_account_id(value: str) -> str:
    """Return a clean ``act_<digits>`` id, or raise.

    Dror pastes the id into ClickUp by hand, so accept the two things he is likely
    to type — the bare digits, or the full ``act_...`` — and reject anything else
    loudly. A pasted Ads Manager URL contains other numbers (business id, a section
    id), so guessing a digit run out of it would silently query the wrong account;
    that must fail here, not 400 later inside a retry.
    """
    text = (value or "").strip()
    if not text:
        raise MetaError("empty ad account id")
    if _ACT_ID.match(text):
        return text
    if text.isdigit():
        return f"act_{text}"
    raise MetaError(
        f"ad account id {value!r} is not an act_ id or a bare number. "
        f"Paste the id from Ads Manager (e.g. act_1234567890 or 1234567890), "
        f"not a URL."
    )


class MetaAdsClient(BaseClient):
    system = "meta_ads"

    def __init__(self, *, dry_run: bool = False):
        super().__init__(dry_run=dry_run)
        if not dry_run:
            self.base_url = config.get("META_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
            self.token = config.require("META_ACCESS_TOKEN")

    # ------------------------------------------------------------------ account

    def account(self, ad_account_id: str) -> dict[str, Any]:
        """The account's ``{"id", "name", "currency"}``."""
        act = normalize_account_id(ad_account_id)
        if self.dry_run:
            self._record("account", ad_account_id=act)
            return {"id": act, "name": "מכללת דוגמה", "currency": "ILS"}
        body = self._request(
            "GET",
            f"{self.base_url}/{act}",
            params={"fields": "name,currency", "access_token": self.token},
        ).json()
        return {
            "id": act,
            "name": str(body.get("name") or ""),
            "currency": str(body.get("currency") or ""),
        }

    # ----------------------------------------------------------------- insights

    def insights(
        self,
        ad_account_id: str,
        *,
        since: str,
        until: str,
        level: str = "campaign",
    ) -> list[dict[str, Any]]:
        """Raw insights rows for the account over ``[since, until]``.

        An explicit ``time_range`` (JSON-encoded, as Graph wants it), never a
        ``date_preset`` — presets are relative to today and would report the wrong
        month on any re-run or back-report. Returns the rows verbatim;
        :func:`campaign_metrics.summarize` does the rest.
        """
        act = normalize_account_id(ad_account_id)
        if self.dry_run:
            self._record("insights", ad_account_id=act, since=since, until=until, level=level)
            return _MOCK_INSIGHTS
        params = {
            "level": level,
            "time_range": json.dumps({"since": since, "until": until}),
            "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions,account_currency",
            "limit": "100",
            "access_token": self.token,
        }
        return self._paged(f"{self.base_url}/{act}/insights", params)

    # ------------------------------------------------- business management (admin)
    # Used by src/tools/sync_meta_accounts.py, not by the report. These need the
    # token to also carry `business_management`, and they write — so they live here
    # but are only ever driven by the admin tool, never by an automation.

    def me(self) -> dict[str, str]:
        """The token's own identity — the system user ``{"id", "name"}``."""
        if self.dry_run:
            self._record("me")
            return {"id": "100000000000001", "name": "Automation"}
        body = self._request(
            "GET", f"{self.base_url}/me",
            params={"fields": "id,name", "access_token": self.token},
        ).json()
        return {"id": str(body.get("id") or ""), "name": str(body.get("name") or "")}

    def assigned_account_ids(self) -> set[str]:
        """The numeric ids of ad accounts already assigned to the system user."""
        if self.dry_run:
            self._record("assigned_account_ids")
            return {"100000000000001"}
        rows = self._paged(
            f"{self.base_url}/me/assigned_ad_accounts",
            {"fields": "account_id", "access_token": self.token, "limit": "100"},
        )
        return {str(r.get("account_id")) for r in rows if r.get("account_id")}

    def reachable_accounts(self, business_id: str) -> list[dict[str, str]]:
        """Every ad account the business can reach — partner-shared and owned.

        ``[{"account_id", "name"}]``, de-duplicated. This is the set the sync tool
        diffs against :meth:`assigned_account_ids` to find what to assign.
        """
        if self.dry_run:
            self._record("reachable_accounts", business_id=business_id)
            return [{"account_id": "100000000000002", "name": "מכללת דוגמה"}]
        seen: dict[str, str] = {}
        for edge in ("client_ad_accounts", "owned_ad_accounts"):
            rows = self._paged(
                f"{self.base_url}/{business_id}/{edge}",
                {"fields": "account_id,name", "access_token": self.token, "limit": "100"},
            )
            for r in rows:
                aid = str(r.get("account_id") or "")
                if aid:
                    seen[aid] = str(r.get("name") or "")
        return [{"account_id": k, "name": v} for k, v in seen.items()]

    def assign_account(
        self, account_id: str, system_user_id: str, *, tasks: tuple[str, ...] = ("ANALYZE",)
    ) -> dict[str, Any]:
        """Assign an ad account to the system user (``ANALYZE`` = read/report)."""
        act = normalize_account_id(account_id)
        if self.dry_run:
            return self._record("assign_account", account_id=act, user=system_user_id, tasks=tasks)
        return self._request(
            "POST", f"{self.base_url}/{act}/assigned_users",
            params={
                "user": system_user_id,
                "tasks": json.dumps(list(tasks)),
                "access_token": self.token,
            },
        ).json()

    def _paged(self, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Follow ``paging.next`` (a full URL with the cursor baked in) to the end."""
        rows: list[dict[str, Any]] = []
        next_url: str | None = url
        next_params: dict[str, Any] | None = params
        for _ in range(MAX_PAGES):
            if not next_url:
                break
            try:
                body = self._request("GET", next_url, params=next_params).json()
            except Exception as exc:  # noqa: BLE001 - surface Meta's message, not a raw HTTP dump
                raise MetaError(f"Meta insights request failed: {exc}") from exc
            rows.extend(body.get("data", []))
            # `paging.next` already carries the cursor and the token as query
            # string, so it is fetched with no extra params.
            next_url = (body.get("paging") or {}).get("next")
            next_params = None
        return rows


# Canned insights for dry-run: one campaign with leads, one paused at zero, so the
# full path — lead extraction and the zero-division guard — is exercised offline.
_MOCK_INSIGHTS: list[dict[str, Any]] = [
    {
        "campaign_id": "111",
        "campaign_name": "וובינר — לידים",
        "spend": "5095.17",
        "impressions": "31066",
        "clicks": "916",
        "account_currency": "ILS",
        "actions": [
            {"action_type": "link_click", "value": "916"},
            {"action_type": "lead", "value": "84"},
            {"action_type": "onsite_conversion.lead_grouped", "value": "84"},
        ],
    },
    {
        "campaign_id": "222",
        "campaign_name": "ריטרגטינג — מושהה",
        "spend": "0",
        "impressions": "0",
        "clicks": "0",
        "account_currency": "ILS",
    },
]

"""ManyChat client — WhatsApp contacts + Flow sending, over the official Meta API.

ManyChat is the WhatsApp system (it replaced Green API — see CLAUDE.md "History").
The number, once connected to ManyChat, can only be messaged *through* ManyChat's
API, and outside the 24-hour customer-service window WhatsApp delivers **only
Meta-approved templates** — which ManyChat sends as **Flows**. So this client does
not send free text: it finds/creates a subscriber and triggers a Flow by id.

Three operations, matching ManyChat's public REST API (``api.manychat.com``):

  * :meth:`find_subscriber` — ``GET /fb/subscriber/findBySystemField?phone=``
  * :meth:`create_subscriber` — ``POST /fb/subscriber/createSubscriber``
  * :meth:`send_flow` — ``POST /fb/sending/sendFlow``

Creating a subscriber via the API records ``MANYCHAT_CONSENT_PHRASE`` as the opt-in
proof Meta requires — it must truthfully describe how the person consented.

Dry-run records the intended call and returns a canned response, so the whole
lead → contact → message path runs with no ManyChat account and no network.

Endpoint paths and field names follow ManyChat's documented shape; confirm them
against the current ManyChat API docs before the first live run.
"""

from __future__ import annotations

from typing import Any, Optional

from .. import config
from ..http import HttpError
from .base import BaseClient


def to_e164(raw: str, default_cc: str = "972") -> str:
    """Normalise a phone number to E.164 (``+<country><number>``).

    Smoove sends Israeli numbers in local form (``050-123-4567``); ManyChat wants
    E.164. Handles a leading ``+``, an international ``00`` prefix, a local ``0``
    trunk (replaced with the default country code), or a number that already
    starts with the country code. Returns ``""`` for anything with no digits.
    """
    raw = (raw or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    if raw.startswith("+"):
        return f"+{digits}"
    if digits.startswith("00"):
        return f"+{digits[2:]}"
    if digits.startswith("0"):
        return f"+{default_cc}{digits[1:]}"
    if digits.startswith(default_cc):
        return f"+{digits}"
    # No trunk zero and no country code we recognise: assume it already carries
    # its own country code rather than silently prepending Israel's.
    return f"+{digits}"


class ManyChatClient(BaseClient):
    system = "manychat"

    def __init__(self, *, dry_run: bool = False):
        super().__init__(dry_run=dry_run)
        if not dry_run:
            self.base_url = config.get(
                "MANYCHAT_BASE_URL", "https://api.manychat.com"
            ).rstrip("/")
            self.api_key = config.require("MANYCHAT_API_KEY")
            self.consent_phrase = config.get("MANYCHAT_CONSENT_PHRASE", "")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def find_subscriber(self, phone: str) -> Optional[str]:
        """Return the subscriber id for ``phone``, or ``None`` if not found.

        ManyChat answers a miss with a 4xx rather than an empty success, so a
        non-retryable client error here means "no such subscriber", not a failure.
        """
        if self.dry_run:
            self._record("find_subscriber", phone=phone)
            return None  # mock: treat as new so the create+send path is exercised
        url = f"{self.base_url}/fb/subscriber/findBySystemField"
        try:
            resp = self._request(
                "GET", url, headers=self._headers(), params={"phone": phone}
            )
        except HttpError as exc:
            if exc.status in (400, 404):
                return None
            raise
        data = (resp.json() or {}).get("data")
        if not data:
            return None
        sub_id = data.get("id") if isinstance(data, dict) else None
        return str(sub_id) if sub_id else None

    def create_subscriber(self, phone: str, first_name: str = "") -> str:
        """Create a WhatsApp subscriber and return its id.

        ``consent_phrase`` is Meta's required opt-in proof — recorded, not
        decorative. Sourced from ``MANYCHAT_CONSENT_PHRASE``.
        """
        if self.dry_run:
            self._record(
                "create_subscriber", phone=phone, first_name=first_name
            )
            return "manychat-sub-mock"
        url = f"{self.base_url}/fb/subscriber/createSubscriber"
        body: dict[str, Any] = {
            "whatsapp_phone": phone,
            "has_opt_in_sms": False,
            "consent_phrase": self.consent_phrase,
        }
        if first_name:
            body["first_name"] = first_name
        resp = self._request("POST", url, headers=self._headers(), json=body)
        data = (resp.json() or {}).get("data") or {}
        sub_id = data.get("id")
        if not sub_id:
            raise RuntimeError(
                f"ManyChat createSubscriber returned no id for {phone}: {resp.text[:300]}"
            )
        return str(sub_id)

    def ensure_subscriber(self, phone: str, first_name: str = "") -> tuple[str, bool]:
        """Find the subscriber for ``phone`` or create one.

        Returns ``(subscriber_id, created)`` — ``created`` is ``True`` only when a
        new contact was made, so the caller can log which path it took.
        """
        existing = self.find_subscriber(phone)
        if existing:
            return existing, False
        return self.create_subscriber(phone, first_name), True

    def send_flow(self, subscriber_id: str, flow_ns: str) -> dict[str, Any]:
        """Trigger a Flow (a Meta-approved template) for a subscriber."""
        if self.dry_run:
            return self._record(
                "send_flow", subscriber_id=subscriber_id, flow_ns=flow_ns
            )
        url = f"{self.base_url}/fb/sending/sendFlow"
        resp = self._request(
            "POST",
            url,
            headers=self._headers(),
            json={"subscriber_id": subscriber_id, "flow_ns": flow_ns},
        )
        return resp.json()

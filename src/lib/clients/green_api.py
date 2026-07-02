"""Green API client — WhatsApp messaging.

Message *bodies* come from :mod:`src.lib.whatsapp_templates` (editable by Dror);
this client only sends. Endpoint shape follows Green API's documented REST format.
Dry-run records the outbound message instead of sending it.
"""

from __future__ import annotations

from typing import Any

from .. import config
from .base import BaseClient


def _chat_id(phone: str) -> str:
    """Green API addresses individual chats as ``<digits>@c.us``."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"{digits}@c.us"


class GreenApiClient(BaseClient):
    system = "green_api"

    def __init__(self, *, dry_run: bool = False):
        super().__init__(dry_run=dry_run)
        if not dry_run:
            self.base_url = config.get(
                "GREEN_API_BASE_URL", "https://api.green-api.com"
            ).rstrip("/")
            self.id_instance = config.require("GREEN_API_ID_INSTANCE")
            self.api_token = config.require("GREEN_API_TOKEN_INSTANCE")

    def send_message(self, phone: str, message: str) -> dict[str, Any]:
        """Send a WhatsApp text message to a phone number."""
        if self.dry_run:
            self._record("send_message", phone=phone, message=message)
            return {"idMessage": "green-msg-mock"}
        url = (
            f"{self.base_url}/waInstance{self.id_instance}"
            f"/sendMessage/{self.api_token}"
        )
        resp = self._request(
            "POST", url, json={"chatId": _chat_id(phone), "message": message}
        )
        return resp.json()

    def create_group(self, group_name: str, phones: list[str]) -> dict[str, Any]:
        """Open a WhatsApp group (onboarding channel) with the given members.

        Feasibility of a per-client group/channel is flagged in the proposal
        ("לפי היתכנות") — Open Question #5.
        """
        if self.dry_run:
            self._record("create_group", group_name=group_name, phones=phones)
            return {"chatId": "group-mock@g.us", "groupName": group_name}
        url = (
            f"{self.base_url}/waInstance{self.id_instance}"
            f"/createGroup/{self.api_token}"
        )
        resp = self._request(
            "POST",
            url,
            json={
                "groupName": group_name,
                "chatIds": [_chat_id(p) for p in phones],
            },
        )
        return resp.json()

"""Taskey CRM client.

The CRM is the hub of Dror's workflow — it triggers automations (status changes)
and receives their results (Drive links, Morning status, an automation log).

**Important:** Taskey's public API is unconfirmed (Open Question #1). This client
is therefore an *abstraction*: automations depend only on the methods here, never
on Taskey specifics. The live path is a generic REST adapter driven by
``CRM_BASE_URL`` + ``CRM_API_TOKEN`` with endpoint paths that are provisional
assumptions; swap in the real adapter once Taskey's API is documented. Dry-run
returns realistic fixtures so all dependent automations are testable today.
"""

from __future__ import annotations

from typing import Any, Optional

from .. import config
from .base import BaseClient

# Primary statuses (proposal §2.1)
STATUS_LEAD = "lead"
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_FINISHED = "finished"

# Secondary statuses (proposal §2.1)
SUB_INITIAL_MEETING = "initial_meeting"
SUB_QUESTIONNAIRE_SENT = "questionnaire_sent"
SUB_QUOTE_SENT = "quote_sent"
SUB_SIGNED = "signed"
SUB_IN_WORK = "in_work"


def _fixture_client(client_id: str) -> dict[str, Any]:
    """A representative CRM record used for dry-run/tests."""
    return {
        "id": client_id,
        "name": "מכללת דוגמה",
        "first_name": "אבי",
        "phone": "+972500000000",
        "email": "client@example.com",
        "status": STATUS_ACTIVE,
        "sub_status": SUB_SIGNED,
        "monthly_price": 3500,
        "service_type": "ניהול קמפיינים",
        "drive_folder_path": "",
        "drive_folder_url": "",
        "signed_contract_url": "",
        "recordings_path": "",
        "morning_status": "",
        "morning_client_id": "",
        "social_profiles": {
            "instagram": "https://instagram.com/example",
            "tiktok": "https://tiktok.com/@example",
        },
        "questionnaire_answers": {},
    }


class CrmClient(BaseClient):
    system = "taskey_crm"

    def __init__(self, *, dry_run: bool = False):
        super().__init__(dry_run=dry_run)
        if not dry_run:
            self.base_url = config.require("CRM_BASE_URL").rstrip("/")
            self.token = config.require("CRM_API_TOKEN")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def get_client(self, client_id: str) -> dict[str, Any]:
        if self.dry_run:
            self._record("get_client", client_id=client_id)
            return _fixture_client(client_id)
        resp = self._request(
            "GET", f"{self.base_url}/clients/{client_id}", headers=self._headers()
        )
        return resp.json()

    def list_active_clients(self) -> list[dict[str, Any]]:
        """Return all clients whose primary status is ``active``."""
        if self.dry_run:
            self._record("list_active_clients")
            return [_fixture_client("1001"), _fixture_client("1002")]
        resp = self._request(
            "GET",
            f"{self.base_url}/clients",
            headers=self._headers(),
            params={"status": STATUS_ACTIVE},
        )
        return resp.json().get("items", resp.json())

    def update_fields(self, client_id: str, **fields: Any) -> dict[str, Any]:
        """Patch fields on a CRM record (Drive path, contract link, etc.)."""
        if self.dry_run:
            return self._record("update_fields", client_id=client_id, fields=fields)
        resp = self._request(
            "PATCH",
            f"{self.base_url}/clients/{client_id}",
            headers=self._headers(),
            json=fields,
        )
        return resp.json()

    def append_automation_log(self, client_id: str, message: str) -> dict[str, Any]:
        """Append a line to the client's automation log shown inside the CRM."""
        if self.dry_run:
            return self._record(
                "append_automation_log", client_id=client_id, message=message
            )
        resp = self._request(
            "POST",
            f"{self.base_url}/clients/{client_id}/automation-log",
            headers=self._headers(),
            json={"message": message},
        )
        return resp.json()

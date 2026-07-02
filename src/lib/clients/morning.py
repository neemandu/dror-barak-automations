"""Morning (getmorning / חשבונית ירוקה) client — invoicing & payment requests.

Used to create a client and issue a monthly payment request ("דרישת תשלום").
Endpoint paths follow Morning's REST API shape and are provisional pending
confirmation (Open Question #4). Dry-run returns canned ids + a payment URL.
"""

from __future__ import annotations

from typing import Any, Optional

from .. import config
from .base import BaseClient

# Morning document type 400 = payment request ("דרישת תשלום").
DOC_TYPE_PAYMENT_REQUEST = 400


class MorningClient(BaseClient):
    system = "morning"

    def __init__(self, *, dry_run: bool = False):
        super().__init__(dry_run=dry_run)
        if not dry_run:
            self.base_url = config.get(
                "MORNING_BASE_URL", "https://api.greeninvoice.co.il/api/v1"
            ).rstrip("/")
            self.api_key = config.require("MORNING_API_KEY")
            self.api_secret = config.require("MORNING_API_SECRET")
            self._jwt: Optional[str] = None

    def _auth_headers(self) -> dict[str, str]:
        if self._jwt is None:
            resp = self._request(
                "POST",
                f"{self.base_url}/account/token",
                json={"id": self.api_key, "secret": self.api_secret},
            )
            self._jwt = resp.json()["token"]
        return {"Authorization": f"Bearer {self._jwt}"}

    def create_client(
        self, name: str, *, email: Optional[str] = None, phone: Optional[str] = None
    ) -> dict[str, Any]:
        if self.dry_run:
            call = self._record("create_client", name=name, email=email, phone=phone)
            return {"id": "morning-client-mock", **call}
        resp = self._request(
            "POST",
            f"{self.base_url}/clients",
            headers=self._auth_headers(),
            json={"name": name, "emails": [email] if email else [], "phone": phone},
        )
        return resp.json()

    def create_payment_request(
        self,
        *,
        morning_client_id: str,
        amount: float,
        description: str,
    ) -> dict[str, Any]:
        """Create a payment request and return the doc + its payment URL."""
        if self.dry_run:
            self._record(
                "create_payment_request",
                morning_client_id=morning_client_id,
                amount=amount,
                description=description,
            )
            return {
                "id": "morning-doc-mock",
                "url": {"origin": "https://pay.greeninvoice.co.il/mock"},
                "amount": amount,
            }
        payload = {
            "type": DOC_TYPE_PAYMENT_REQUEST,
            "client": {"id": morning_client_id},
            "income": [{"description": description, "amount": amount, "quantity": 1}],
        }
        resp = self._request(
            "POST",
            f"{self.base_url}/documents",
            headers=self._auth_headers(),
            json=payload,
        )
        return resp.json()

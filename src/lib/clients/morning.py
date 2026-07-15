"""Morning (getmorning / חשבונית ירוקה) client — clients and monthly billing.

Creates a client and issues the monthly **חשבון עסקה** (proforma) that Dror emails
out on the 1st.

Document type matters here, and getting it wrong is not a cosmetic bug: these are
tax documents.

  300  חשבון עסקה        a request to pay. Issuing it says nothing about revenue.
  305  חשבונית מס         a tax invoice: recognises the income.
  320  חשבונית מס/קבלה    invoice + receipt together.
  400  קבלה               a RECEIPT: says the money has arrived.

This module previously issued **400** while calling it a "payment request" — a
receipt for money nobody had paid, which would misstate Dror's revenue to his
accountant and the tax authority. It was never run live. It issues 300 now.

Dry-run returns canned ids + a payment URL.
"""

from __future__ import annotations

from typing import Any, Optional

from .. import config
from .base import BaseClient

# חשבון עסקה — a request to pay, not a receipt and not yet a tax invoice.
DOC_TYPE_PROFORMA = 300
# Kept for reference; 320/400/405 additionally require a `payment` array, because
# a document that says money arrived has to say how.
DOC_TYPE_TAX_INVOICE = 305
DOC_TYPE_RECEIPT = 400


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

    def create_proforma(
        self,
        *,
        morning_client_id: str,
        amount: float,
        description: str,
        client_email: Optional[str] = None,
    ) -> dict[str, Any]:
        """Issue a חשבון עסקה and return the document, with links to it.

        ``amount`` is **ex-VAT** — Morning adds מע״מ itself, which is why ClickUp's
        price field is `מחיר חודשי ללא מעמ`. Passing a VAT-inclusive figure would
        quietly bill every client 18% too much.
        """
        if self.dry_run:
            self._record(
                "create_proforma",
                morning_client_id=morning_client_id,
                amount=amount,
                description=description,
            )
            return {
                "id": "morning-doc-mock",
                "number": 1001,
                "url": {"origin": "https://pay.greeninvoice.co.il/mock",
                        "he": "https://app.greeninvoice.co.il/documents/mock"},
                "amount": amount,
            }
        payload: dict[str, Any] = {
            "type": DOC_TYPE_PROFORMA,
            "client": {"id": morning_client_id},
            "income": [{
                "description": description,
                "quantity": 1,
                "price": amount,
                # 1 = the price is before VAT, which Morning then adds.
                "vatType": 1,
            }],
            # We email it ourselves, with Dror's wording. Morning's own mail would
            # arrive alongside it saying something different.
            "lang": "he",
            "currency": "ILS",
        }
        if client_email:
            payload["client"]["emails"] = [client_email]
        resp = self._request(
            "POST",
            f"{self.base_url}/documents",
            headers=self._auth_headers(),
            json=payload,
        )
        return resp.json()

    def download_document(self, doc_id: str) -> bytes:
        """The document's PDF, for attaching to Dror's email."""
        if self.dry_run:
            self._record("download_document", doc_id=doc_id)
            return b"%PDF-1.4 mock proforma"
        # Morning hands back a short-lived link rather than the bytes.
        link = self._request(
            "GET",
            f"{self.base_url}/documents/{doc_id}/download/links",
            headers=self._auth_headers(),
        ).json()
        url = link.get("he") or link.get("origin") or link.get("link")
        if not url:
            raise RuntimeError(f"Morning gave no download link for document {doc_id}")
        return self._request("GET", url).content

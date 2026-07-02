"""Fillout client — send a quote/contract with a digital-signature field.

Flow: create a prefilled submission link for the quote form and send it to the
client. When the client signs, Fillout fires a webhook (handled in
``src/webhook_server.py``); we then fetch the submission and its signed PDF.

Form ids and the signature field are provisional (Open Question #3). Dry-run
returns a canned link + PDF bytes.
"""

from __future__ import annotations

from typing import Any, Optional

from .. import config
from .base import BaseClient


class FilloutClient(BaseClient):
    system = "fillout"

    def __init__(self, *, dry_run: bool = False):
        super().__init__(dry_run=dry_run)
        if not dry_run:
            self.base_url = config.get(
                "FILLOUT_BASE_URL", "https://api.fillout.com/v1/api"
            ).rstrip("/")
            self.api_key = config.require("FILLOUT_API_KEY")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def create_quote_link(
        self, form_id: str, prefill: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a prefilled quote/contract link for the client to sign."""
        if self.dry_run:
            self._record("create_quote_link", form_id=form_id, prefill=prefill)
            return {
                "submissionId": "fillout-sub-mock",
                "url": f"https://forms.fillout.com/t/{form_id}?mock=1",
            }
        resp = self._request(
            "POST",
            f"{self.base_url}/forms/{form_id}/submissions/prefill",
            headers=self._headers(),
            json=prefill,
        )
        return resp.json()

    def get_submission(self, form_id: str, submission_id: str) -> dict[str, Any]:
        if self.dry_run:
            self._record(
                "get_submission", form_id=form_id, submission_id=submission_id
            )
            return {"submissionId": submission_id, "status": "completed"}
        resp = self._request(
            "GET",
            f"{self.base_url}/forms/{form_id}/submissions/{submission_id}",
            headers=self._headers(),
        )
        return resp.json()

    def download_signed_pdf(self, form_id: str, submission_id: str) -> bytes:
        """Fetch the signed submission as PDF bytes."""
        if self.dry_run:
            self._record(
                "download_signed_pdf", form_id=form_id, submission_id=submission_id
            )
            return b"%PDF-1.4 mock signed document\n"
        resp = self._request(
            "GET",
            f"{self.base_url}/forms/{form_id}/submissions/{submission_id}/pdf",
            headers=self._headers(),
        )
        return resp.content

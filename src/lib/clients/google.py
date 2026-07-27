"""Google Workspace client — Contacts, Drive, Forms.

Covers the Google touch-points across the automations:
  * Contacts — save a new lead's phone number (People API).
  * Drive — create the client folder, copy templates, upload signed PDFs/reports.
  * Forms — read questionnaire responses.

Live calls authenticate with a service account impersonating Dror — see
:mod:`src.lib.google_auth` for why, and docs/GOOGLE_SETUP.md for the setup. Tokens
are minted per call from the cache rather than held on the instance: a Lambda
container can outlive a token's one-hour life, and a client built at import time
would then carry a dead token forever.

Dry-run returns canned ids/urls. Note: employee hour-tracking Sheets are
deliberately **not** touched (out of scope).
"""

from __future__ import annotations

from typing import Any, Optional

from .. import google_auth
from .base import BaseClient


class GoogleClient(BaseClient):
    system = "google"

    def __init__(self, *, dry_run: bool = False):
        super().__init__(dry_run=dry_run)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {google_auth.access_token()}"}

    # --- Contacts (People API) -------------------------------------------
    def create_contact(
        self, name: str, phone: str, *, email: Optional[str] = None
    ) -> dict[str, Any]:
        if self.dry_run:
            self._record("create_contact", name=name, phone=phone, email=email)
            return {"resourceName": "people/mock"}
        body: dict[str, Any] = {
            "names": [{"givenName": name}],
            "phoneNumbers": [{"value": phone}],
        }
        if email:
            body["emailAddresses"] = [{"value": email}]
        resp = self._request(
            "POST",
            "https://people.googleapis.com/v1/people:createContact",
            headers=self._headers(),
            json=body,
        )
        return resp.json()

    # --- Drive ------------------------------------------------------------
    def create_folder(self, name: str, parent_id: str) -> dict[str, Any]:
        if self.dry_run:
            self._record("create_folder", name=name, parent_id=parent_id)
            fid = "drive-folder-mock"
            return {"id": fid, "webViewLink": f"https://drive.google.com/drive/folders/{fid}"}
        resp = self._request(
            "POST",
            "https://www.googleapis.com/drive/v3/files",
            headers=self._headers(),
            params={"fields": "id,webViewLink", "supportsAllDrives": "true"},
            json={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
        )
        return resp.json()

    def list_folder(self, parent_id: str) -> list[dict[str, Any]]:
        """The folder's own (non-trashed) children — ``id``, ``name``, ``mimeType``.

        What makes onboarding re-runnable: it copies templates and creates
        subfolders into a folder that may already hold them (a retry after a
        half-finished run, or Dror pressing the button again). Asking Drive what
        is already there is cheaper and more honest than a store of what we think
        we did.
        """
        if self.dry_run:
            self._record("list_folder", parent_id=parent_id)
            return []
        out: list[dict[str, Any]] = []
        page_token: Optional[str] = None
        while True:
            params = {
                "q": f"'{parent_id}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType)",
                "pageSize": "200",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            body = self._request(
                "GET",
                "https://www.googleapis.com/drive/v3/files",
                headers=self._headers(),
                params=params,
            ).json()
            out.extend(body.get("files", []))
            page_token = body.get("nextPageToken")
            if not page_token:
                return out

    def file_name(self, file_id: str) -> str:
        """A file's own name — so a copy can be called after its template rather
        than after its 33-character Drive id."""
        if self.dry_run:
            self._record("file_name", file_id=file_id)
            return f"תבנית {file_id}"
        return str(
            self._request(
                "GET",
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                headers=self._headers(),
                params={"fields": "name", "supportsAllDrives": "true"},
            ).json().get("name")
            or file_id
        )

    def copy_file(self, file_id: str, new_name: str, parent_id: str) -> dict[str, Any]:
        if self.dry_run:
            self._record(
                "copy_file", file_id=file_id, new_name=new_name, parent_id=parent_id
            )
            return {"id": "drive-copy-mock", "name": new_name}
        resp = self._request(
            "POST",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/copy",
            headers=self._headers(),
            params={"fields": "id,name", "supportsAllDrives": "true"},
            json={"name": new_name, "parents": [parent_id]},
        )
        return resp.json()

    def upload_file(
        self, name: str, content: bytes, parent_id: str, mime_type: str
    ) -> dict[str, Any]:
        if self.dry_run:
            self._record(
                "upload_file",
                name=name,
                parent_id=parent_id,
                mime_type=mime_type,
                size=len(content),
            )
            fid = "drive-file-mock"
            return {"id": fid, "webViewLink": f"https://drive.google.com/file/d/{fid}/view"}
        # Two-step: create metadata, then upload media (simplified).
        meta = self._request(
            "POST",
            "https://www.googleapis.com/drive/v3/files",
            headers=self._headers(),
            params={"fields": "id", "supportsAllDrives": "true"},
            json={"name": name, "parents": [parent_id]},
        ).json()
        self._request(
            "PATCH",
            f"https://www.googleapis.com/upload/drive/v3/files/{meta['id']}",
            headers={**self._headers(), "Content-Type": mime_type},
            params={"uploadType": "media", "supportsAllDrives": "true"},
            data=content,
        )
        link = self._request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{meta['id']}",
            headers=self._headers(),
            params={"fields": "id,webViewLink", "supportsAllDrives": "true"},
        ).json()
        return link

    # --- Forms ------------------------------------------------------------
    def get_form_response(self, form_id: str, response_id: str) -> dict[str, Any]:
        if self.dry_run:
            self._record(
                "get_form_response", form_id=form_id, response_id=response_id
            )
            return {
                "responseId": response_id,
                "answers": {
                    "instagram": {"textAnswers": {"answers": [{"value": "https://instagram.com/example"}]}},
                    "tiktok": {"textAnswers": {"answers": [{"value": "https://tiktok.com/@example"}]}},
                },
            }
        resp = self._request(
            "GET",
            f"https://forms.googleapis.com/v1/forms/{form_id}/responses/{response_id}",
            headers=self._headers(),
        )
        return resp.json()

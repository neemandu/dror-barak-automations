"""The client's Drive folder — created once, found thereafter.

Two things need this folder and they happen in the wrong order for either to own
it: the signing page files the signed PDF at the moment of signing, and onboarding
runs afterwards, because signing is what sets `חתם` and `חתם` is what triggers
onboarding. Whoever gets there first must create it; the other must find it.

So it is idempotent and shared. The folder's link lives in the `נתיב לגוגל דרייב`
column on the ClickUp task, which is both where Dror looks for it and how we avoid
making a second one.

Ownership matters: the folder is created while impersonating Dror, so it is his,
appears in his Drive, and survives us.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from . import config, google_auth
from .http import request

DRIVE = "https://www.googleapis.com/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"

_FOLDER_ID = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")


def folder_id_from(value: str) -> Optional[str]:
    """The folder id inside a Drive URL, or a bare id if that is what we were given."""
    text = (value or "").strip()
    if not text:
        return None
    match = _FOLDER_ID.search(text)
    if match:
        return match.group(1)
    # Dror may have pasted a bare id rather than a link.
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", text):
        return text
    return None


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {google_auth.access_token()}"}


def _exists(folder_id: str) -> bool:
    try:
        body = request(
            "GET", f"{DRIVE}/{folder_id}", headers=_headers(),
            params={"fields": "id,trashed,mimeType", "supportsAllDrives": "true"},
        ).json()
        return body.get("mimeType") == FOLDER_MIME and not body.get("trashed")
    except Exception:  # noqa: BLE001 - a missing or unreadable folder is "no"
        return False


def ensure(crm: Any, client: dict[str, Any], *, dry_run: bool = False) -> dict[str, str]:
    """Return ``{"id", "url"}`` for the client's folder, creating it if needed.

    Reuses whatever `נתיב לגוגל דרייב` already holds — a second folder for the same
    client is worse than no automation, because now Dror has two places to look and
    neither is complete.
    """
    client_id = str(client.get("id") or "")
    name = str(client.get("name") or client_id)

    recorded = str(client.get("drive_folder_url") or client.get("drive_folder_path") or "")
    existing = folder_id_from(recorded)
    if existing and (dry_run or _exists(existing)):
        return {"id": existing, "url": recorded or f"https://drive.google.com/drive/folders/{existing}",
                "created": False}

    if dry_run:
        return {"id": "drive-folder-mock", "created": True,
                "url": "https://drive.google.com/drive/folders/drive-folder-mock"}

    parent = config.require("DRIVE_CLIENTS_PARENT_ID")
    folder = request(
        "POST", DRIVE, headers=_headers(),
        params={"fields": "id,webViewLink", "supportsAllDrives": "true"},
        json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
    ).json()

    # Record it immediately: a folder we created but did not write back is a folder
    # the next run will create again.
    crm.update_fields(client_id, drive_folder_url=folder["webViewLink"])
    return {"id": folder["id"], "url": folder["webViewLink"], "created": True}

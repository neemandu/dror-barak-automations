"""Finished strategies Dror keeps as examples, shown to the strategy agent.

The strategy agent's instructions say it learns structure, depth and tone from example
documents. They live in Dror's Drive: Google Docs in a ``דוגמאות אסטרטגיה`` folder inside
the templates folder (a subfolder, so they are never offered as client templates). He adds
or removes a Doc there and the next strategy follows it. Nothing of a client's is kept in
this repository.
"""

from __future__ import annotations

from . import config
from .clients.google import GoogleClient
from .http import request
from .logging_setup import get_logger

FOLDER = "דוגמאות אסטרטגיה"
DOC_MIME = "application/vnd.google-apps.document"
#: Enough for several examples; the strategy itself is the long part of the call.
MAX_CHARS = 40_000

_log = get_logger("strategy_examples", "load")

INTRO = ("\n\n---\nדוגמאות לאסטרטגיות שדרור כבר כתב. למד מהן את המבנה, רמת הפירוט, הסגנון וסוגי "
         "הטבלאות והמשפכים, לא את הפרטים: אל תעתיק מהן עובדות, שמות או מספרים של לקוחות אחרים.\n")


def load(google: GoogleClient | None = None) -> str:
    """The examples as prompt text, or "" (no folder, no Docs, or Drive unreachable)."""
    parent = config.get("DRIVE_TEMPLATES_FOLDER_ID")
    if not parent:
        return ""
    try:
        google = google or GoogleClient()
        folder = next((f for f in google.list_folder(str(parent))
                       if f.get("name") == FOLDER and f.get("mimeType") == "application/vnd.google-apps.folder"), None)
        if not folder:
            return ""
        parts: list[str] = []
        for doc in google.list_folder(str(folder["id"])):
            if doc.get("mimeType") != DOC_MIME:
                continue
            resp = request("GET", f"https://www.googleapis.com/drive/v3/files/{doc['id']}/export",
                           headers=google._headers(), params={"mimeType": "text/plain"})
            text = resp.content.decode("utf-8-sig", errors="replace").strip()
            if text:
                parts.append(f"## דוגמה: {doc.get('name')}\n{text}")
        body = "\n\n".join(parts)[:MAX_CHARS]
        return INTRO + body if body else ""
    except Exception as exc:  # noqa: BLE001 - examples improve a strategy; their absence must not block it
        _log.warning("examples_unavailable", extra={"error": str(exc)})
        return ""

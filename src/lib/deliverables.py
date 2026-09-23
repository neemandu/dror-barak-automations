"""Where the AI's documents go: an editable Google Doc in the client's folder.

The strategy and the social prep report used to be ``.md`` files — the strategy
in the client's folder, the prep report in a shared default folder, because it
looked for a folder field the CRM never returns. A text file is not something
Dror edits and sends; a Google Doc is. Both now land in the client's own
``אסטרטגיה`` subfolder.
"""

from __future__ import annotations

from typing import Any

from . import client_folder, markdown_html, pdf


def save_html_doc(crm: Any, client: dict[str, Any], title: str, html: str, *,
                  subfolder: str = "אסטרטגיה", dry_run: bool = False) -> dict[str, str]:
    """Store ``html`` as a Google Doc named ``title``; returns ``{"id", "url"}``."""
    if dry_run:
        return {"id": "doc-mock", "url": "https://docs.google.com/document/d/doc-mock/edit"}
    from .clients.google import GoogleClient

    folder = client_folder.ensure(crm, client)
    subs = client_folder.ensure_subfolders(GoogleClient(), folder["id"])
    parent = (subs.get(subfolder) or {}).get("id") or folder["id"]
    doc = pdf.html_to_google_doc(html, title, parent)
    return {"id": str(doc["id"]),
            "url": str(doc.get("webViewLink") or f"https://docs.google.com/document/d/{doc['id']}/edit")}


def save_markdown_doc(crm: Any, client: dict[str, Any], title: str, markdown: str, *,
                      subfolder: str = "אסטרטגיה", dry_run: bool = False) -> dict[str, str]:
    """Convert ``markdown`` to a Google Doc named ``title``; returns ``{"id", "url"}``."""
    return save_html_doc(crm, client, title, markdown_html.to_document(title, markdown),
                         subfolder=subfolder, dry_run=dry_run)

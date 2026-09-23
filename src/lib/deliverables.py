"""Where the system's documents go: an editable Google Doc in the client's folder.

The strategy, the social prep report and the questionnaire answers are built as
branded Word files (:mod:`branded_doc`: the brand band on every page, Dror's
details and the page number in the footer) and converted by Drive into Google
Docs in the client's own ``אסטרטגיה`` subfolder, where Dror edits them before
anything reaches the client.
"""

from __future__ import annotations

from typing import Any

from . import branded_doc, client_folder, pdf


def save_doc(crm: Any, client: dict[str, Any], *, file_name: str, title: str, subtitle: str,
             blocks: list[branded_doc.Block], subfolder: str = "אסטרטגיה",
             dry_run: bool = False) -> dict[str, str]:
    """Store the document as a Google Doc named ``file_name``; returns ``{"id", "url"}``."""
    data = branded_doc.build(title, subtitle, blocks)  # built in dry-run too: it must not fail live
    if dry_run:
        return {"id": "doc-mock", "url": "https://docs.google.com/document/d/doc-mock/edit"}
    from .clients.google import GoogleClient

    folder = client_folder.ensure(crm, client)
    subs = client_folder.ensure_subfolders(GoogleClient(), folder["id"])
    parent = (subs.get(subfolder) or {}).get("id") or folder["id"]
    doc = pdf.file_to_google_doc(data, branded_doc.DOCX_TYPE, file_name, parent)
    return {"id": str(doc["id"]),
            "url": str(doc.get("webViewLink") or f"https://docs.google.com/document/d/{doc['id']}/edit")}


def save_markdown_doc(crm: Any, client: dict[str, Any], markdown: str, *, file_name: str,
                      title: str, subtitle: str, subfolder: str = "אסטרטגיה",
                      dry_run: bool = False) -> dict[str, str]:
    """:func:`save_doc` for what Claude writes (Markdown)."""
    return save_doc(crm, client, file_name=file_name, title=title, subtitle=subtitle,
                    blocks=branded_doc.from_markdown(markdown), subfolder=subfolder, dry_run=dry_run)


def prepared_for(client_name: str) -> str:
    """The line under a document's title."""
    return f"הוכן עבור {client_name}   ·   {branded_doc.hebrew_date()}"

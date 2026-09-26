"""The משימות bot's results as Google Docs, and their PDF copies for ClickUp.

Every answer the bot gives on a task is a branded Google Doc (:mod:`branded_doc`,
converted by Drive), one per version, named ``<task> - גרסה N``:

* **with a linked client** in the client's own folder, in a ``משימות`` subfolder
  made on first use (not one of the four standard subfolders: most clients never
  get a task, and onboarding should not give every folder an empty one);
* **without a client** in a ``משימות Claude`` folder at the top of Dror's Drive.

ClickUp's API attaches files, not Drive links, so the task also gets a PDF copy of
each version (:func:`pdf_of`), and the comment links the live Doc. The Doc is also
what a revision reads back (:func:`text_of`): the comment may only show a preview.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from . import branded_doc, config, deliverables

CLIENT_SUBFOLDER = "משימות"
NO_CLIENT_FOLDER = "משימות Claude"

_DOC_ID = re.compile(r"docs\.google\.com/document/d/([A-Za-z0-9_-]+)")


def doc_id_in(text: str) -> Optional[str]:
    """The id of the first Google Doc linked in ``text``."""
    match = _DOC_ID.search(text or "")
    return match.group(1) if match else None


def _child_folder(google: Any, parent_id: str, name: str) -> str:
    """Id of the folder ``name`` directly under ``parent_id``, created if missing."""
    from .client_folder import FOLDER_MIME

    for f in google.list_folder(parent_id):
        if f.get("mimeType") == FOLDER_MIME and f.get("name") == name:
            return str(f["id"])
    return str(google.create_folder(name, parent_id)["id"])


def folder_for(client: Optional[dict[str, Any]], crm: Any) -> str:
    """Where this task's Docs go (see the module docstring)."""
    from .clients.google import GoogleClient

    google = GoogleClient()
    if client:
        from . import client_folder

        folder = client_folder.ensure(crm, client)  # the client's existing folder
        return _child_folder(google, folder["id"], CLIENT_SUBFOLDER)
    parent = str(config.get("DRIVE_DEFAULT_PARENT_ID") or "root")
    return _child_folder(google, parent, NO_CLIENT_FOLDER)


def save(name: str, markdown: str, *, client: Optional[dict[str, Any]], crm: Any,
         dry_run: bool = False) -> dict[str, str]:
    """Write ``markdown`` as a branded Google Doc; returns ``{"id", "url"}``."""
    client_name = str((client or {}).get("name") or "")
    subtitle = deliverables.prepared_for(client_name) if client_name else branded_doc.hebrew_date()
    data = branded_doc.build(name, subtitle, branded_doc.from_markdown(markdown))
    if dry_run:
        return {"id": "task-doc-mock", "url": "https://docs.google.com/document/d/task-doc-mock/edit"}
    from . import pdf

    doc = pdf.file_to_google_doc(data, branded_doc.DOCX_TYPE, name, folder_for(client, crm))
    return {"id": str(doc["id"]),
            "url": str(doc.get("webViewLink") or f"https://docs.google.com/document/d/{doc['id']}/edit")}


def _export(doc_id: str, mime: str) -> bytes:
    from . import pdf
    from .http import request

    return request("GET", f"{pdf.DRIVE}/{doc_id}/export", headers=pdf._headers(),
                   params={"mimeType": mime}).content


def pdf_of(doc_id: str) -> bytes:
    return _export(doc_id, "application/pdf")


def text_of(doc_id: str) -> str:
    """The answer a version's Doc holds, without the title block :func:`save` put
    on top. A revision reads earlier versions back from here; with the title and
    "הוכן עבור" line left in, the model copied them into the next version."""
    return without_title(_export(doc_id, "text/plain").decode("utf-8-sig", errors="replace"))


_DATE_LINE = re.compile(r"^\d{1,2} ב\S+ \d{4}$")


def without_title(text: str) -> str:
    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip():
        rest = [ln for ln in lines[1:] if ln.strip()]
        if rest and (rest[0].strip().startswith("הוכן עבור") or _DATE_LINE.match(rest[0].strip())):
            cut = lines.index(rest[0], 1) + 1
            return "\n".join(lines[cut:]).strip()
    return text.strip()


def as_plain_text(markdown: str) -> str:
    """Markdown made readable as a ClickUp comment (which does not render it)."""
    lines = []
    for line in markdown.splitlines():
        if re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line) and "-" in line:
            continue  # a table's separator row
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", line)
        if line.strip().startswith("|"):
            line = " · ".join(c.strip() for c in line.strip().strip("|").split("|"))
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

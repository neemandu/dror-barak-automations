"""HTML → PDF, via Google Drive.

Rendering Hebrew RTL to PDF from Python normally means weasyprint or wkhtmltopdf,
which means system libraries (cairo, pango, fontconfig) and a Lambda layer to
carry them — plus fonts that actually cover Hebrew, or the contract prints as
boxes.

Drive already does all of that. Upload HTML, let Drive convert it to a Doc, export
the Doc as PDF. It costs two API calls we are already authenticated for, and it
handles the typography properly because Google Docs is a word processor.

The trade: a PDF now depends on Drive being reachable. We already depend on Drive
for storing the signed contract, so this adds no new system — and the alternative
was a binary dependency that breaks on a runtime upgrade.

The intermediate files are temporary and always cleaned up, including on failure:
a half-converted contract left in Dror's Drive is confusing at best.
"""

from __future__ import annotations

from typing import Any

from . import google_auth
from .http import request

DRIVE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


class PdfError(RuntimeError):
    """Raised when a document could not be converted."""


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {google_auth.access_token()}"}


def html_to_pdf(html: str, *, name: str = "document") -> bytes:
    """Convert an HTML string to PDF bytes."""
    h = _headers()
    uploaded: list[str] = []
    try:
        raw = request(
            "POST", UPLOAD,
            headers={**h, "Content-Type": "text/html; charset=utf-8"},
            params={"uploadType": "media", "fields": "id"},
            data=html.encode("utf-8"),
        ).json()
        uploaded.append(raw["id"])

        # Copying with a Docs mimeType is what performs the conversion.
        doc = request(
            "POST", f"{DRIVE}/{raw['id']}/copy", headers=h, params={"fields": "id"},
            json={"mimeType": "application/vnd.google-apps.document", "name": name},
        ).json()
        uploaded.append(doc["id"])

        pdf = request(
            "GET", f"{DRIVE}/{doc['id']}/export", headers=h,
            params={"mimeType": "application/pdf"},
        ).content

        if not pdf.startswith(b"%PDF-"):
            raise PdfError(f"Drive returned {len(pdf)} bytes that are not a PDF")
        return pdf
    except PdfError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PdfError(f"could not convert to PDF: {exc}") from exc
    finally:
        # Always: a failed conversion must not leave litter in Dror's Drive.
        for fid in uploaded:
            try:
                request("DELETE", f"{DRIVE}/{fid}", headers=h)
            except Exception:  # noqa: BLE001 - cleanup is best-effort
                pass


def html_to_google_doc(html: str, name: str, parent_id: str) -> dict[str, Any]:
    """Convert HTML into a Google Doc kept in ``parent_id``, and return it.

    Same Drive conversion the PDF path uses, but the Doc is the deliverable rather
    than an intermediate — so it is placed in the client's folder, not deleted.
    Dror can then open and edit it, which a flat PDF would not allow. Owned by him,
    because we act as him.
    """
    h = _headers()
    tmp = request(
        "POST", UPLOAD,
        headers={**h, "Content-Type": "text/html; charset=utf-8"},
        params={"uploadType": "media", "fields": "id"},
        data=html.encode("utf-8"),
    ).json()
    try:
        doc = request(
            "POST", f"{DRIVE}/{tmp['id']}/copy", headers=h,
            params={"fields": "id,webViewLink", "supportsAllDrives": "true"},
            json={"mimeType": "application/vnd.google-apps.document",
                  "name": name, "parents": [parent_id]},
        ).json()
        return doc
    finally:
        # The uploaded HTML blob was only the conversion source.
        try:
            request("DELETE", f"{DRIVE}/{tmp['id']}", headers=h)
        except Exception:  # noqa: BLE001
            pass


def upload_pdf(pdf: bytes, name: str, parent_id: str) -> dict[str, Any]:
    """Store a PDF in a Drive folder and return its id and link."""
    h = _headers()
    meta = request(
        "POST", DRIVE, headers=h,
        params={"fields": "id", "supportsAllDrives": "true"},
        json={"name": name, "parents": [parent_id], "mimeType": "application/pdf"},
    ).json()
    request(
        "PATCH", f"{UPLOAD}/{meta['id']}",
        headers={**h, "Content-Type": "application/pdf"},
        params={"uploadType": "media", "supportsAllDrives": "true"},
        data=pdf,
    )
    return request(
        "GET", f"{DRIVE}/{meta['id']}", headers=h,
        params={"fields": "id,name,webViewLink", "supportsAllDrives": "true"},
    ).json()

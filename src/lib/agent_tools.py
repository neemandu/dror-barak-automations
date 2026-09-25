"""Tools the משימות agent can call: Dror's Google Drive and Gmail.

Dror's tasks are marketing and content work, and some of them need his material
("summarise the brief in Drive", "reply to the college that wrote yesterday").
These are the hands for that, acting **as Dror** through the same service account
the other automations use (:mod:`src.lib.google_auth`).

Deliberately limited to what a content task needs — and to what can't hurt:

* **Drive** — search and read. No write, delete, move or sharing: a task
  description is not a reason to change who can see a file. (The bot's own
  answer does become a Doc, but by :mod:`src.lib.task_docs`, not by a tool.)
* **Gmail** — search, read, and create a **draft**. Never send. A ClickUp task
  can be written by anyone with access to the list, and whatever it says, the
  email goes out only when Dror presses Send himself.

Every tool returns text for the model. A failure is returned, not raised (the
agent sees it and can work around it — e.g. Gmail scopes not yet granted), and
each write is logged with its link so it shows on the dashboard and in the daily
email.
"""

from __future__ import annotations

import base64
import json
from email.message import EmailMessage
from typing import Any, Callable, Optional

from . import config, google_auth, text_style

# Enough for a long brief; a whole shared drive dump is not what a task needs,
# and every character is paid for on each later turn of the loop.
MAX_READ_CHARS = 40_000

_DRIVE = "https://www.googleapis.com/drive/v3/files"
_GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"

# Google-native files have no bytes of their own; they are exported.
_EXPORT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "drive_search",
        "description": (
            "Search Dror's Google Drive by file name and content. Returns up to 20 "
            "files with id, name, type, last modified time and link. Use this to "
            "find a brief, a client folder or earlier material before writing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to look for, e.g. a client or campaign name."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "drive_read",
        "description": (
            "Read the text of a Drive file by id (Google Docs, Sheets as CSV, Slides, "
            "and plain-text files). Long files are cut at 40,000 characters. PDFs, "
            "images and folders cannot be read."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    },
    {
        "name": "gmail_search",
        "description": (
            "Search Dror's Gmail with Gmail search syntax (e.g. 'from:college.ac.il "
            "newer_than:7d'). Returns up to 10 messages: id, from, to, subject, date, "
            "snippet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "gmail_read",
        "description": "Read one email by message id: headers and plain-text body.",
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
    {
        "name": "gmail_create_draft",
        "description": (
            "Create an email DRAFT in Dror's Gmail. It is not sent: Dror reviews "
            "and sends it himself. Pass thread_id to draft a reply in an existing "
            "conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient address(es), comma-separated."},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text body."},
                "cc": {"type": "string"},
                "thread_id": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
]


class Toolbox:
    """Runs the tools. ``log`` receives (action, detail, url) for every write."""

    def __init__(self, *, dry_run: bool = False,
                 log: Optional[Callable[[str, str, str], None]] = None):
        self.dry_run = dry_run
        self.log = log or (lambda action, detail, url: None)
        self.calls: list[dict[str, Any]] = []

    def run(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Run tool ``name``. Returns ``(text, is_error)``."""
        self.calls.append({"tool": name, "input": args})
        handler = getattr(self, f"_{name}", None)
        if handler is None or name not in {d["name"] for d in DEFINITIONS}:
            return f"Unknown tool {name!r}.", True
        if self.dry_run:
            return f"[dry-run] {name} would run with {json.dumps(args, ensure_ascii=False)[:300]}", False
        try:
            return handler(**args), False
        except TypeError as exc:
            return f"Bad arguments for {name}: {exc}", True
        except Exception as exc:  # noqa: BLE001 - the model should see it and adapt
            return f"{name} failed: {exc}", True

    # --- HTTP ------------------------------------------------------------
    @staticmethod
    def _get(url: str, *, gmail: bool = False, **kwargs: Any):
        from .http import request

        return request("GET", url, headers=_auth(gmail), **kwargs)

    @staticmethod
    def _post(url: str, *, gmail: bool = False, **kwargs: Any):
        from .http import request

        headers = {**_auth(gmail), **kwargs.pop("headers", {})}
        return request("POST", url, headers=headers, **kwargs)

    # --- Drive -----------------------------------------------------------
    def _drive_search(self, query: str) -> str:
        q = query.replace("\\", "\\\\").replace("'", "\\'")
        data = self._get(_DRIVE, params={
            "q": f"(name contains '{q}' or fullText contains '{q}') and trashed = false",
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
            "pageSize": "20",
            "orderBy": "modifiedTime desc",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }).json()
        files = data.get("files", [])
        if not files:
            return "No files found."
        return json.dumps(files, ensure_ascii=False, indent=1)

    def _drive_read(self, file_id: str) -> str:
        meta = self._get(f"{_DRIVE}/{file_id}", params={
            "fields": "id,name,mimeType,webViewLink", "supportsAllDrives": "true",
        }).json()
        mime = meta.get("mimeType", "")
        if mime in _EXPORT:
            text = _utf8(self._get(f"{_DRIVE}/{file_id}/export",
                                   params={"mimeType": _EXPORT[mime]}))
        elif mime.startswith("text/") or mime in ("application/json", "text/csv"):
            text = _utf8(self._get(f"{_DRIVE}/{file_id}",
                                   params={"alt": "media", "supportsAllDrives": "true"}))
        else:
            return (f"'{meta.get('name')}' is {mime or 'an unknown type'}, which this "
                    f"tool cannot read as text. Link: {meta.get('webViewLink')}")
        return f"File: {meta.get('name')}\n\n{_cut(text)}"

    # --- Gmail -----------------------------------------------------------
    def _gmail_search(self, query: str) -> str:
        found = self._get(f"{_GMAIL}/messages", gmail=True,
                          params={"q": query, "maxResults": "10"}).json()
        out = []
        for ref in found.get("messages", []):
            msg = self._get(f"{_GMAIL}/messages/{ref['id']}", gmail=True, params=[
                ("format", "metadata"), ("metadataHeaders", "From"),
                ("metadataHeaders", "To"), ("metadataHeaders", "Subject"),
                ("metadataHeaders", "Date"),
            ]).json()
            headers = _headers_of(msg)
            out.append({"id": msg.get("id"), "thread_id": msg.get("threadId"),
                        **headers, "snippet": msg.get("snippet", "")})
        if not out:
            return "No messages found."
        return json.dumps(out, ensure_ascii=False, indent=1)

    def _gmail_read(self, message_id: str) -> str:
        msg = self._get(f"{_GMAIL}/messages/{message_id}", gmail=True,
                        params={"format": "full"}).json()
        headers = _headers_of(msg)
        body = _plain_text(msg.get("payload") or {}) or msg.get("snippet", "")
        head = "\n".join(f"{k}: {v}" for k, v in headers.items())
        return f"{head}\nthread_id: {msg.get('threadId')}\n\n{_cut(body)}"

    def _gmail_create_draft(self, to: str, subject: str, body: str,
                            cc: Optional[str] = None,
                            thread_id: Optional[str] = None) -> str:
        # Sent in Dror's name once he presses Send: his house style.
        subject, body = text_style.humanize(subject), text_style.humanize(body)
        mime = EmailMessage()
        mime["To"] = to
        if cc:
            mime["Cc"] = cc
        mime["Subject"] = subject
        mime.set_content(body)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        message: dict[str, Any] = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        draft = self._post(f"{_GMAIL}/drafts", gmail=True,
                           json={"message": message}).json()
        link = "https://mail.google.com/mail/u/0/#drafts"
        self.log("gmail_draft_created", f"{to}: {subject}", link)
        return f"Draft created (id {draft.get('id')}) to {to}, subject '{subject}'. Not sent."


def _auth(gmail: bool) -> dict[str, str]:
    scopes = google_auth.GMAIL_SCOPES if gmail else None
    return {"Authorization": f"Bearer {google_auth.access_token(scopes=scopes)}"}


def _utf8(resp: Any) -> str:
    """Drive's export sends no charset, so ``requests`` guesses Latin-1 and Hebrew
    arrives as mojibake. It is UTF-8 (with a BOM on Docs exports)."""
    return resp.content.decode("utf-8-sig", errors="replace")


def _cut(text: str) -> str:
    if len(text) <= MAX_READ_CHARS:
        return text
    return text[:MAX_READ_CHARS] + f"\n\n[... cut: {len(text) - MAX_READ_CHARS} more characters]"


def _headers_of(msg: dict[str, Any]) -> dict[str, str]:
    wanted = {"from", "to", "cc", "subject", "date"}
    return {h["name"].lower(): h["value"]
            for h in (msg.get("payload") or {}).get("headers", [])
            if h.get("name", "").lower() in wanted}


def _plain_text(part: dict[str, Any]) -> str:
    """The first text/plain body in a (possibly nested) MIME payload."""
    if part.get("mimeType") == "text/plain" and (part.get("body") or {}).get("data"):
        data = part["body"]["data"]
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    for sub in part.get("parts") or []:
        text = _plain_text(sub)
        if text:
            return text
    return ""

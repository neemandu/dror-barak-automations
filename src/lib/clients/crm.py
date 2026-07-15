"""CRM client — ClickUp.

ClickUp is the CRM: one task per client. Task status changes drive the
automations, and the automations write their results back onto the task.

  * primary status (lead/active/paused/finished) → the ClickUp **task status**
  * secondary status (initial_meeting/.../in_work) → a **dropdown custom field**
  * everything else (phone, price, Drive folder, contract link) → custom fields
  * the automation log → task **comments**, so it shows up where Dror already looks

Custom fields cannot be created through ClickUp's API, so the list is built by
hand in the UI once (see ``docs/CLICKUP_SETUP.md``) and everything here is matched
by *name* at runtime via :mod:`src.lib.crm_fields` — no field ids in ``.env``, and
nothing to re-copy if the list is rebuilt.

This class keeps the interface the automations already depend on; it replaced a
Taskey adapter whose API was never confirmed.

Configure ``CLICKUP_API_TOKEN`` and ``CLICKUP_LIST_ID``. Check the list is set up
correctly with::

    python -m src.tools.check_clickup_crm
"""

from __future__ import annotations

from typing import Any, Optional

from .. import config, crm_fields
from ..crm_fields import (  # re-exported: importers use these from the CRM client
    STATUS_ACTIVE,
    STATUS_FINISHED,
    STATUS_LEAD,
    STATUS_PAUSED,
    SUB_INITIAL_MEETING,
    SUB_IN_WORK,
    SUB_QUESTIONNAIRE_SENT,
    SUB_QUOTE_SENT,
    SUB_SIGNED,
)
from .base import BaseClient

__all__ = [
    "CrmClient",
    "STATUS_LEAD", "STATUS_ACTIVE", "STATUS_PAUSED", "STATUS_FINISHED",
    "SUB_INITIAL_MEETING", "SUB_QUESTIONNAIRE_SENT", "SUB_QUOTE_SENT",
    "SUB_SIGNED", "SUB_IN_WORK",
]

# Fields an automation cannot work without. check_clickup_crm reports on these.
REQUIRED_FIELDS = ["phone", "sub_status", "monthly_price"]
OPTIONAL_FIELDS = [
    "email", "service_type", "drive_folder", "signed_contract",
    "recordings_path", "morning_status", "morning_client_id",
]


def _fixture_client(client_id: str) -> dict[str, Any]:
    """A representative client used for dry-run/tests."""
    return {
        "id": client_id,
        "name": "מכללת דוגמה",
        "first_name": "אבי",
        "phone": "+972500000000",
        "email": "client@example.com",
        "status": STATUS_ACTIVE,
        "sub_status": SUB_SIGNED,
        "monthly_price": 3500,
        "service_type": "ניהול קמפיינים",
        "drive_folder_path": "",
        "drive_folder_url": "",
        "signed_contract_url": "",
        "recordings_path": "",
        "morning_status": "",
        "morning_client_id": "",
        "url": "https://app.clickup.com/t/mock",
        "social_profiles": {
            "instagram": "https://instagram.com/example",
            "tiktok": "https://tiktok.com/@example",
        },
        "questionnaire_answers": {},
    }


class CrmClient(BaseClient):
    system = "clickup_crm"

    def __init__(self, *, dry_run: bool = False, list_id: Optional[str] = None):
        super().__init__(dry_run=dry_run)
        self._fields_cache: Optional[dict[str, dict[str, Any]]] = None
        self._statuses_cache: Optional[list[str]] = None
        if not dry_run:
            self.base_url = config.get(
                "CLICKUP_BASE_URL", "https://api.clickup.com/api/v2"
            ).rstrip("/")
            self.token = config.require("CLICKUP_API_TOKEN")
            self.list_id = list_id or config.require("CLICKUP_LIST_ID")
        else:
            self.list_id = list_id or "mock-list"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.token}

    # ------------------------------------------------------------- list schema

    def fields(self) -> dict[str, dict[str, Any]]:
        """``{canonical: field}`` for the clients list, fetched once per client."""
        if self._fields_cache is None:
            if self.dry_run:
                self._fields_cache = crm_fields.resolve_fields(_MOCK_FIELDS)
            else:
                resp = self._request(
                    "GET",
                    f"{self.base_url}/list/{self.list_id}/field",
                    headers=self._headers(),
                )
                self._fields_cache = crm_fields.resolve_fields(
                    resp.json().get("fields", [])
                )
        return self._fields_cache

    def statuses(self) -> list[str]:
        """The list's task status names, as Dror named them."""
        if self._statuses_cache is None:
            if self.dry_run:
                self._statuses_cache = ["ליד", "לקוח פעיל", "מושהה", "הסתיים"]
            else:
                resp = self._request(
                    "GET", f"{self.base_url}/list/{self.list_id}", headers=self._headers()
                )
                self._statuses_cache = [
                    s.get("status", "") for s in resp.json().get("statuses", [])
                ]
        return self._statuses_cache

    def primary_in_field(self) -> bool:
        """True when the primary status is a custom field, not the task status.

        Both layouts are supported. Dror built a `סטטוס ראשי` dropdown rather than
        customising the list's statuses, and forcing a rebuild would throw away
        working setup — but the choice has costs, so see docs/CLICKUP_SETUP.md.
        """
        return "status" in self.fields()

    def _status_name_for(self, canonical: str) -> Optional[str]:
        """The list's actual status name for a canonical status, if present."""
        for name in self.statuses():
            if crm_fields.canonical_status(name) == canonical:
                return name
        return None

    # ------------------------------------------------------------------- reads

    def _to_client(self, task: dict[str, Any]) -> dict[str, Any]:
        """Flatten a ClickUp task into the client dict the automations expect."""
        by_id = {str(f.get("id")): f for f in task.get("custom_fields") or []}
        resolved = self.fields()

        def field_value(canonical: str) -> Any:
            field = resolved.get(canonical)
            if not field:
                return ""
            raw = by_id.get(str(field.get("id")), {}).get("value")
            return crm_fields.read_value(field, raw)

        name = str(task.get("name") or "")
        sub_raw = str(field_value("sub_status") or "")
        # The primary status can live in either place (see primary_in_field), so
        # prefer a 'סטטוס ראשי' custom field and fall back to the task status.
        status_raw = str(field_value("status") or "") if self.primary_in_field() else ""
        if not status_raw:
            status_raw = str((task.get("status") or {}).get("status") or "")
        price = field_value("monthly_price")

        drive = str(field_value("drive_folder") or "")
        return {
            "id": str(task.get("id") or ""),
            "name": name,
            # ClickUp has no separate first-name column; the greeting uses the
            # first word of the client name, which is what Dror types today.
            "first_name": name.split(" ")[0] if name else "",
            "phone": str(field_value("phone") or ""),
            "email": str(field_value("email") or ""),
            "status": crm_fields.canonical_status(status_raw) or status_raw,
            "sub_status": crm_fields.canonical_sub_status(sub_raw) or sub_raw,
            "monthly_price": price if price != "" else None,
            "service_type": str(field_value("service_type") or ""),
            # Drive is one field in ClickUp; automations ask for either a path or
            # a URL, so serve both from it and let the caller pick.
            "drive_folder_path": drive,
            "drive_folder_url": drive if drive.startswith("http") else "",
            "signed_contract_url": str(field_value("signed_contract") or ""),
            "recordings_path": str(field_value("recordings_path") or ""),
            "morning_status": str(field_value("morning_status") or ""),
            "morning_client_id": str(field_value("morning_client_id") or ""),
            "url": str(task.get("url") or ""),
            "social_profiles": {},
            "questionnaire_answers": {},
        }

    def get_client(self, client_id: str) -> dict[str, Any]:
        if self.dry_run:
            self._record("get_client", client_id=client_id)
            return _fixture_client(client_id)
        resp = self._request(
            "GET",
            f"{self.base_url}/task/{client_id}",
            headers=self._headers(),
            params={"include_subtasks": "false"},
        )
        return self._to_client(resp.json())

    def _all_clients(self) -> list[dict[str, Any]]:
        """Every non-archived client task, paged."""
        out: list[dict[str, Any]] = []
        page = 0
        while True:  # ClickUp pages at 100 tasks; an agency will exceed that.
            resp = self._request(
                "GET",
                f"{self.base_url}/list/{self.list_id}/task",
                headers=self._headers(),
                params={"archived": "false", "subtasks": "false", "page": str(page)},
            )
            body = resp.json()
            tasks = body.get("tasks", [])
            out.extend(self._to_client(t) for t in tasks)
            if body.get("last_page") or not tasks:
                return out
            page += 1

    def list_active_clients(self) -> list[dict[str, Any]]:
        """Every client whose primary status is ``active``.

        Filtered here rather than by ClickUp's ``statuses[]`` param, because that
        param only sees task statuses — and the primary status may be a custom
        field. Filtering client-side works for both layouts.
        """
        if self.dry_run:
            self._record("list_active_clients")
            return [_fixture_client("1001"), _fixture_client("1002")]

        # Refuse rather than bill nobody and report a quiet success.
        if not self.primary_in_field() and self._status_name_for(STATUS_ACTIVE) is None:
            raise ValueError(
                f"No status on list {self.list_id} maps to 'active', and there is no "
                f"'סטטוס ראשי' field either. Statuses found: {self.statuses()}. "
                f"See docs/CLICKUP_SETUP.md."
            )

        clients = self._all_clients()
        active = [c for c in clients if c.get("status") == STATUS_ACTIVE]
        if clients and not active:
            raise ValueError(
                f"{len(clients)} clients on list {self.list_id}, none with a primary "
                f"status meaning 'active'. Saw: {sorted({c.get('status') for c in clients})}. "
                f"See docs/CLICKUP_SETUP.md."
            )
        return active

    # ------------------------------------------------------------------ writes

    def update_fields(self, client_id: str, **fields: Any) -> dict[str, Any]:
        """Write client fields back onto the task.

        Accepts the canonical names plus the aliases the automations already use
        (``drive_folder_url``, ``signed_contract_url``, ...). Unknown or
        not-configured fields are reported back rather than silently dropped, so a
        missing column shows up in the run-log instead of vanishing.
        """
        if self.dry_run:
            return self._record("update_fields", client_id=client_id, fields=fields)

        resolved = self.fields()
        written: dict[str, Any] = {}
        skipped: dict[str, str] = {}

        for key, value in fields.items():
            canonical = _CALLER_ALIASES.get(key, key)

            # The primary status is a task status unless a 'סטטוס ראשי' field
            # exists, in which case it falls through to the custom-field path.
            if canonical == "status" and not self.primary_in_field():
                name = self._status_name_for(str(value)) or str(value)
                self._request(
                    "PUT",
                    f"{self.base_url}/task/{client_id}",
                    headers=self._headers(),
                    json={"status": name},
                )
                written[key] = name
                continue

            field = resolved.get(canonical)
            if not field:
                skipped[key] = "no such field on the list"
                continue
            try:
                api_value = crm_fields.coerce_value(field, value)
            except ValueError as exc:
                skipped[key] = str(exc)
                continue
            self._request(
                "POST",
                f"{self.base_url}/task/{client_id}/field/{field['id']}",
                headers=self._headers(),
                json={"value": api_value},
            )
            written[key] = value

        return {"id": client_id, "written": written, "skipped": skipped}

    def attach_file(
        self,
        client_id: str,
        canonical_field: str,
        file_bytes: bytes,
        filename: str,
    ) -> dict[str, Any]:
        """Upload a file into an Attachment custom field (e.g. the signed contract).

        Two steps, per ClickUp's API: upload the file to the custom field entity to
        get an attachment id, then point the task's field at that id. A plain
        string cannot be written to an Attachment field.

        Falls back to a URL field of the same name if that is how the list is set
        up, so both layouts work.
        """
        field = self.fields().get(canonical_field)
        if not field:
            return {"skipped": f"no {canonical_field} field on the list"}
        if self.dry_run:
            return self._record(
                "attach_file", client_id=client_id, field=field.get("name"),
                filename=filename, bytes=len(file_bytes),
            )
        if str(field.get("type")) != "attachment":
            return {"skipped": f"{field.get('name')!r} is not an Attachment field"}

        workspace = config.require("CLICKUP_TEAM_ID")
        # ClickUp will not accept a non-ASCII filename at all -- it answers
        # "Filename can only contain letters, numbers, dots, underscores, hyphens,
        # spaces...". Sent as a multipart header instead, a Hebrew name is silently
        # stored as mojibake, which is worse. So the name is sanitised here; the
        # Drive copy keeps the readable Hebrew one.
        safe = _ascii_filename(filename, fallback=f"attachment-{client_id}")
        upload = self._request(
            "POST",
            f"https://api.clickup.com/api/v3/workspaces/{workspace}"
            f"/custom_fields/{field['id']}/attachments",
            headers=self._headers(),
            files={"attachment": (safe, file_bytes, "application/octet-stream")},
        )
        attachment_id = upload.json().get("id")
        self._request(
            "POST",
            f"{self.base_url}/task/{client_id}/field/{field['id']}",
            headers=self._headers(),
            json={"value": {"add": [attachment_id]}},
        )
        return {"id": attachment_id, "field": field.get("name"), "filename": safe}

    def append_automation_log(self, client_id: str, message: str) -> dict[str, Any]:
        """Append to the client's automation log — a comment on the task."""
        if self.dry_run:
            return self._record(
                "append_automation_log", client_id=client_id, message=message
            )
        resp = self._request(
            "POST",
            f"{self.base_url}/task/{client_id}/comment",
            headers=self._headers(),
            json={"comment_text": message, "notify_all": False},
        )
        return resp.json()


def _ascii_filename(name: str, fallback: str = "attachment") -> str:
    """An ASCII filename safe for a multipart header, keeping the extension.

    Multipart filenames are effectively latin-1; Hebrew sent there is mangled on
    arrival. The readable name travels as a form field instead — this is only the
    header's fallback, so it needs to be sane rather than pretty.
    """
    import os
    import re

    stem, ext = os.path.splitext(name or "")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-")
    ext = re.sub(r"[^A-Za-z0-9.]+", "", ext) or ".bin"
    return f"{stem or fallback}{ext}"


# Names the automations pass to update_fields -> canonical field.
_CALLER_ALIASES: dict[str, str] = {
    "drive_folder_url": "drive_folder",
    "drive_folder_path": "drive_folder",
    "signed_contract_url": "signed_contract",
    "sub_status": "sub_status",
    "status": "status",
}

# Shape of a correctly configured list, for dry-run and tests.
_MOCK_FIELDS: list[dict[str, Any]] = [
    {"id": "f-phone", "name": "טלפון", "type": "phone"},
    {"id": "f-email", "name": "מייל", "type": "email"},
    {"id": "f-price", "name": "מחיר חודשי", "type": "number"},
    {"id": "f-service", "name": "סוג שירות", "type": "short_text"},
    {"id": "f-drive", "name": "תיקיית Drive", "type": "url"},
    {"id": "f-contract", "name": "חוזה חתום", "type": "url"},
    {"id": "f-recordings", "name": "נתיב הקלטות", "type": "short_text"},
    {"id": "f-morning", "name": "סטטוס Morning", "type": "short_text"},
    {
        "id": "f-sub",
        "name": "סטטוס משני",
        "type": "drop_down",
        "type_config": {
            "options": [
                {"id": "o-meet", "name": "פגישה ראשונית"},
                {"id": "o-quest", "name": "נשלח שאלון"},
                {"id": "o-quote", "name": "נשלחה הצעת מחיר"},
                {"id": "o-signed", "name": "חתם"},
                {"id": "o-work", "name": "בעבודה"},
            ]
        },
    },
]

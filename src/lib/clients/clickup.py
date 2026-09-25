"""ClickUp client — task details for the משימות → Claude automation.

Fetches a task and posts a comment back with Claude's draft. Dry-run returns a
canned task.
"""

from __future__ import annotations

from typing import Any

from .. import config
from .base import BaseClient


class ClickUpClient(BaseClient):
    system = "clickup"

    def __init__(self, *, dry_run: bool = False):
        super().__init__(dry_run=dry_run)
        if not dry_run:
            self.base_url = config.get(
                "CLICKUP_BASE_URL", "https://api.clickup.com/api/v2"
            ).rstrip("/")
            self.token = config.require("CLICKUP_API_TOKEN")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.token}

    def get_task(self, task_id: str) -> dict[str, Any]:
        if self.dry_run:
            self._record("get_task", task_id=task_id)
            return {
                "id": task_id,
                "name": "כתוב פוסט לקמפיין החדש",
                "description": "צריך פוסט לאינסטגרם עם CTA להרשמה לוובינר.",
                "status": {"status": "to do"},
            }
        resp = self._request(
            "GET", f"{self.base_url}/task/{task_id}", headers=self._headers()
        )
        return resp.json()

    def list_comments(self, task_id: str) -> list[dict[str, Any]]:
        """The task's comments, oldest first (ClickUp returns newest first)."""
        if self.dry_run:
            self._record("list_comments", task_id=task_id)
            return []
        resp = self._request(
            "GET", f"{self.base_url}/task/{task_id}/comment", headers=self._headers()
        )
        comments = resp.json().get("comments", [])
        return sorted(comments, key=lambda c: int(c.get("date") or 0))

    def list_replies(self, comment_id: str) -> list[dict[str, Any]]:
        """The replies in a comment's thread, oldest first. They are not part of
        :meth:`list_comments`, which returns only top-level comments."""
        if self.dry_run:
            self._record("list_replies", comment_id=comment_id)
            return []
        resp = self._request(
            "GET", f"{self.base_url}/comment/{comment_id}/reply", headers=self._headers()
        )
        replies = resp.json().get("comments", [])
        return sorted(replies, key=lambda c: int(c.get("date") or 0))

    def reply(self, comment_id: str, text: str) -> dict[str, Any]:
        """Post ``text`` in the thread under ``comment_id``."""
        if self.dry_run:
            return self._record("reply", comment_id=comment_id, text=text)
        resp = self._request(
            "POST", f"{self.base_url}/comment/{comment_id}/reply",
            headers=self._headers(), json={"comment_text": text, "notify_all": False},
        )
        return resp.json()

    def attach(self, task_id: str, data: bytes, filename: str,
               content_type: str = "application/pdf") -> dict[str, Any]:
        """Attach a file to the task itself (its attachments, not a custom field).

        ``filename`` must be ASCII: ClickUp refuses anything else outright.
        """
        if self.dry_run:
            return self._record("attach", task_id=task_id, filename=filename, bytes=len(data))
        resp = self._request(
            "POST", f"{self.base_url}/task/{task_id}/attachment", headers=self._headers(),
            files={"attachment": (filename, data, content_type)},
        )
        return resp.json()

    def comment(self, task_id: str, text: str) -> dict[str, Any]:
        if self.dry_run:
            return self._record("comment", task_id=task_id, text=text)
        resp = self._request(
            "POST",
            f"{self.base_url}/task/{task_id}/comment",
            headers=self._headers(),
            json={"comment_text": text},
        )
        return resp.json()

    def get_list_fields(self, list_id: str) -> list[dict[str, Any]]:
        """Return the custom fields defined on a list (id, name, type)."""
        if self.dry_run:
            self._record("get_list_fields", list_id=list_id)
            # A couple of fields exist; the rest of the CRM data falls back to
            # the task description so the dry-run shows both paths.
            return [
                {"id": "field-price", "name": "מחיר חודשי", "type": "number"},
                {"id": "field-service", "name": "סוג שירות", "type": "text"},
            ]
        resp = self._request(
            "GET", f"{self.base_url}/list/{list_id}/field", headers=self._headers()
        )
        return resp.json().get("fields", [])

    def create_task(
        self,
        list_id: str,
        name: str,
        *,
        description: str | None = None,
        custom_fields: list[dict[str, Any]] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Create a task in a list. ``custom_fields`` is ``[{"id","value"}]``."""
        if self.dry_run:
            self._record(
                "create_task",
                list_id=list_id,
                name=name,
                custom_fields=custom_fields,
                status=status,
            )
            return {"id": "clickup-task-mock", "url": "https://app.clickup.com/t/mock"}
        body: dict[str, Any] = {"name": name}
        if description:
            body["description"] = description
        if custom_fields:
            body["custom_fields"] = custom_fields
        if status:
            body["status"] = status
        resp = self._request(
            "POST",
            f"{self.base_url}/list/{list_id}/task",
            headers=self._headers(),
            json=body,
        )
        return resp.json()

"""ClickUp client — read task details for the Claude Code bridge (bonus module).

The bonus automation turns a ClickUp task into a Claude Code run. This client
fetches a task and can post a comment back with the result. Dry-run returns a
canned task. What "hand to Claude Code" should concretely do is Open Question #11.
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

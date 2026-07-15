"""Idempotency — making sure a webhook that arrives twice only acts once.

ClickUp retries deliveries it thinks failed, and a slow response or a Lambda
timeout can produce the same event twice. Without a guard, a duplicated `חתם`
event runs onboarding twice: two Drive folders, two Morning clients, two
"welcome" messages. Those are not idempotent operations and they are not
retractable, so the guard has to sit in front of them.

There are **two layers**, and both are needed:

1. **Delivery dedup** (:func:`claim`) — the same webhook delivery, seen twice.
   Keyed on ClickUp's ``history_items[].id``, which is unique per change.

2. **Business dedup** (:func:`guard`) — *different* deliveries that mean the same
   thing. Dror moving a client `חתם → בעבודה → חתם` produces two genuinely
   distinct events, and layer 1 will happily let both through. Layer 2 asks "have
   we already onboarded this client?" rather than "have we seen this message?"

Claims are recorded in DynamoDB (``IDEMPOTENCY_TABLE``) so they are shared across
Lambda instances. Locally, and in dry-run, a JSON file stands in — the same
semantics, no AWS required.

A claim is deliberately released when the work fails, so ClickUp's retry can do
the job. The alternative — leaving the claim in place — would turn one transient
error into a permanently skipped onboarding.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from . import config

DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60  # a week: far longer than any retry window

STATE_IN_PROGRESS = "in_progress"
STATE_DONE = "done"


class _FileStore:
    """Local stand-in for DynamoDB. Single-process; fine for dev and tests."""

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or config.get("IDEMPOTENCY_PATH", "logs/idempotency.json"))

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def claim(self, key: str, ttl: int) -> bool:
        data = self._read()
        entry = data.get(key)
        now = time.time()
        if entry and entry.get("expires_at", 0) > now:
            return False
        data[key] = {"state": STATE_IN_PROGRESS, "expires_at": now + ttl}
        self._write(data)
        return True

    def complete(self, key: str) -> None:
        data = self._read()
        if key in data:
            data[key]["state"] = STATE_DONE
            self._write(data)

    def release(self, key: str) -> None:
        data = self._read()
        data.pop(key, None)
        self._write(data)


class _DynamoStore:
    """DynamoDB-backed claims, shared across Lambda instances."""

    def __init__(self, table_name: str):
        import boto3  # imported lazily: local runs need no AWS SDK

        self.table = boto3.resource(
            "dynamodb", region_name=config.get("AWS_REGION", "il-central-1")
        ).Table(table_name)

    def claim(self, key: str, ttl: int) -> bool:
        from botocore.exceptions import ClientError

        now = int(time.time())
        try:
            # The conditional put IS the lock: two concurrent deliveries race here
            # and exactly one wins. Checking-then-writing would let both through.
            self.table.put_item(
                Item={
                    "pk": key,
                    "state": STATE_IN_PROGRESS,
                    "claimed_at": now,
                    "expires_at": now + ttl,
                },
                ConditionExpression="attribute_not_exists(pk) OR expires_at < :now",
                ExpressionAttributeValues={":now": now},
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def complete(self, key: str) -> None:
        self.table.update_item(
            Key={"pk": key},
            UpdateExpression="SET #s = :done",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":done": STATE_DONE},
        )

    def release(self, key: str) -> None:
        self.table.delete_item(Key={"pk": key})


def _store():
    table = config.get("IDEMPOTENCY_TABLE")
    return _DynamoStore(table) if table else _FileStore()


def claim(key: str, *, ttl: int = DEFAULT_TTL_SECONDS) -> bool:
    """Try to claim ``key``. ``True`` means we won it and should do the work.

    ``False`` means someone already has it — a duplicate delivery — so do nothing.
    """
    return _store().claim(key, ttl)


def complete(key: str) -> None:
    """Mark a claim finished. The record stays, so later duplicates are rejected."""
    _store().complete(key)


def release(key: str) -> None:
    """Give a claim back after a failure, so a retry can pick the work up."""
    _store().release(key)


def event_key(payload: dict[str, Any], raw_body: str = "") -> str:
    """A stable id for one ClickUp delivery.

    ``history_items[].id`` is unique per change, which is exactly what we want: the
    same change redelivered has the same id, while a genuinely new change does not.
    Falls back to hashing the body when a payload carries no history.
    """
    items = payload.get("history_items") or []
    ids = [str(i.get("id")) for i in items if i.get("id")]
    if ids:
        return f"evt:{payload.get('event')}:{payload.get('task_id')}:{'-'.join(sorted(ids))}"
    import hashlib

    digest = hashlib.sha256((raw_body or json.dumps(payload, sort_keys=True)).encode()).hexdigest()
    return f"evt:{payload.get('event')}:{payload.get('task_id')}:{digest[:32]}"


def guard(automation: str, client_id: str, marker: str = "") -> str:
    """Key for "this automation already ran for this client".

    Layer 2. Use for work that must not happen twice regardless of how many
    distinct events ask for it — onboarding being the obvious one, since it
    creates a Drive folder and a Morning client that nobody wants duplicated.
    """
    suffix = f":{marker}" if marker else ""
    return f"once:{automation}:{client_id}{suffix}"

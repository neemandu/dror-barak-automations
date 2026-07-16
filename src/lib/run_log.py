"""Run-log — the record of everything the automations did.

Every automation writes one entry per action, through ``Automation.log_action``.
This is the **only** source for two things Dror asked for:

  * the dashboard (:mod:`src.dashboard`) — grouped by subject, with links out
  * the daily email (:mod:`src.automations.daily_email`) — what ran today

and the answer to "is anything actually happening?", which is the thing the whole
brief is about. There is no other copy of this history.

**Two backends.** A JSON Lines file locally, DynamoDB when ``RUN_LOG_TABLE`` is
set. The file is not an option on Lambda: its disk is per-instance and temporary,
so entries written there are thrown away seconds later and the dashboard shows an
empty page forever.

DynamoDB layout: partition by day, sort by timestamp. The dashboard asks for "the
last N days", which is then N small queries rather than a scan of everything.
Writing everything to one partition would be simpler and would eventually make
every read hot on a single key.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from . import config

# Entries expire after a year: long enough that Dror can look back over a client's
# whole engagement, short enough that the table doesn't grow forever. This is an
# activity log, not the system of record — Drive and ClickUp hold the artefacts.
TTL_DAYS = 400


def record(
    automation: str,
    action: str,
    status: str = "ok",
    *,
    client_id: Optional[str] = None,
    dry_run: bool = False,
    detail: Optional[str] = None,
    **extra: Any,
) -> dict[str, Any]:
    """Append one entry and return it.

    ``status`` is typically ``ok`` / ``skipped`` / ``error``. ``detail`` is a
    short human-readable note; ``extra`` carries structured context.

    Never raises: logging must not be able to fail the work it describes. A lost
    log line is a gap in the dashboard; a failed onboarding is a client without a
    Drive folder.
    """
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "automation": automation,
        "action": action,
        "status": status,
        "client_id": client_id,
        "dry_run": dry_run,
        "detail": detail,
    }
    entry.update(extra)
    try:
        _store().write(entry)
    except Exception as exc:  # noqa: BLE001
        # Say so on stderr rather than silently losing history.
        print(json.dumps({"level": "ERROR", "msg": "run_log_write_failed",
                          "error": str(exc), "entry": entry.get("action")},
                         ensure_ascii=False))
    return entry


def read_all() -> list[dict[str, Any]]:
    return _store().read_since(datetime.now(timezone.utc) - timedelta(days=TTL_DAYS))


def read_since(since: datetime) -> list[dict[str, Any]]:
    """Entries at or after ``since`` (tz-aware), oldest first."""
    return _store().read_since(since)


# ----------------------------------------------------------------- backends


class _FileStore:
    """JSON Lines on disk. Local development and tests."""

    def _path(self) -> Path:
        path = Path(config.get("RUN_LOG_PATH", "logs/run_log.jsonl"))
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write(self, entry: dict[str, Any]) -> None:
        with self._path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_since(self, since: datetime) -> list[dict[str, Any]]:
        out = []
        for entry in self._iter():
            ts = _parse_ts(entry.get("ts"))
            if ts is not None and ts >= since:
                out.append(entry)
        return out

    def _iter(self) -> Iterator[dict[str, Any]]:
        path = self._path()
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # a half-written line must not break the dashboard


class _DynamoStore:
    """DynamoDB. What runs on Lambda, where a file would evaporate."""

    def __init__(self, table_name: str):
        import boto3

        self.table = boto3.resource(
            "dynamodb", region_name=config.get("AWS_REGION", "eu-central-1")
        ).Table(table_name)

    def write(self, entry: dict[str, Any]) -> None:
        ts = str(entry["ts"])
        item = {
            "day": ts[:10],  # 2026-07-16
            # Sort key: the timestamp alone would collide when two automations log
            # in the same millisecond, and one would silently overwrite the other.
            "ts_id": f"{ts}#{uuid.uuid4().hex[:8]}",
            "expires_at": int(datetime.now(timezone.utc).timestamp()) + TTL_DAYS * 86400,
        }
        for key, value in entry.items():
            if value is None or value == "":
                continue  # DynamoDB keeps nulls; the readers treat absent as empty
            item[key] = _for_dynamo(value)
        self.table.put_item(Item=item)

    def read_since(self, since: datetime) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        out: list[dict[str, Any]] = []
        for day in _days_from(since):
            resp = self.table.query(KeyConditionExpression=Key("day").eq(day))
            out.extend(resp.get("Items", []))
            while "LastEvaluatedKey" in resp:
                resp = self.table.query(
                    KeyConditionExpression=Key("day").eq(day),
                    ExclusiveStartKey=resp["LastEvaluatedKey"],
                )
                out.extend(resp.get("Items", []))

        entries = [_from_dynamo(i) for i in out]
        # The day partition is coarse: `since` may land mid-day.
        entries = [e for e in entries
                   if (t := _parse_ts(e.get("ts"))) is not None and t >= since]
        return sorted(entries, key=lambda e: str(e.get("ts")))


def _days_from(since: datetime) -> list[str]:
    """Every day partition from `since` to today, inclusive."""
    today = datetime.now(timezone.utc).date()
    day = since.astimezone(timezone.utc).date()
    out = []
    while day <= today:
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _for_dynamo(value: Any) -> Any:
    """DynamoDB has no float type; everything else survives as-is."""
    from decimal import Decimal

    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, (list, tuple)):
        return [_for_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: _for_dynamo(v) for k, v in value.items()}
    return value


def _from_dynamo(item: dict[str, Any]) -> dict[str, Any]:
    """Back to plain Python, dropping the storage-only keys."""
    from decimal import Decimal

    out = {}
    for key, value in item.items():
        if key in ("day", "ts_id", "expires_at"):
            continue
        if isinstance(value, Decimal):
            value = int(value) if value == value.to_integral_value() else float(value)
        out[key] = value
    # The readers index these directly; absent must read as empty, not KeyError.
    out.setdefault("client_id", None)
    out.setdefault("detail", None)
    out.setdefault("dry_run", False)
    return out


def _store() -> Any:
    table = config.get("RUN_LOG_TABLE")
    return _DynamoStore(table) if table else _FileStore()


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

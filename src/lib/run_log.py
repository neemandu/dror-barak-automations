"""Run-log — the append-only record every automation writes to.

Each automation appends a structured record of what it did (or, in dry-run, what
it *would* do). This log feeds two things Dror asked for:

  * the CRM automation log (so each client shows what ran), and
  * the daily WhatsApp summary (:mod:`src.automations.daily_summary`).

Stored as JSON Lines at ``RUN_LOG_PATH`` (default ``logs/run_log.jsonl``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from . import config


def _log_path() -> Path:
    path = Path(config.get("RUN_LOG_PATH", "logs/run_log.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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
    """Append one run-log entry and return it.

    ``status`` is typically ``ok`` / ``skipped`` / ``error``. ``detail`` is a
    short human-readable note; ``extra`` carries structured context.
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
    with _log_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_all() -> list[dict[str, Any]]:
    return list(_iter_entries())


def read_since(since: datetime) -> list[dict[str, Any]]:
    """Return entries with a timestamp at or after ``since`` (tz-aware)."""
    out = []
    for entry in _iter_entries():
        ts = _parse_ts(entry.get("ts"))
        if ts is not None and ts >= since:
            out.append(entry)
    return out


def _iter_entries() -> Iterator[dict[str, Any]]:
    path = _log_path()
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

"""Structured JSON logging.

Every automation logs structured records (not bare ``print``) with a timestamp,
level, the automation name, a per-run ``run_id``, and arbitrary context fields,
so a single run can be traced end to end.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """Render log records as one JSON object per line."""

    # Attributes the stdlib puts on every LogRecord; everything else is context.
    _RESERVED = set(
        vars(logging.makeLogRecord({})).keys()
    ) | {"message", "asctime", "taskName"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# Names the stdlib already puts on a LogRecord. Passing one as context makes
# logging raise KeyError, which would take the automation down with it.
_RECORD_ATTRS = set(vars(logging.makeLogRecord({})).keys()) | {
    "message", "asctime", "taskName",
}


class _ContextAdapter(logging.LoggerAdapter):
    """Adds the bound context to each record *without* dropping per-call extras.

    ``logging.LoggerAdapter.process`` assigns ``kwargs["extra"] = self.extra``,
    which silently throws away the ``extra=`` passed at the call site. That made
    every log line carry only the automation name and run id — no reason on a
    rejected webhook, no client_id, no detail — which is precisely the context
    you need when reading production logs.

    Context whose name collides with a built-in LogRecord attribute is suffixed
    rather than passed through: ``logging`` raises ``KeyError`` on a collision,
    and a log line must never be able to fail the work it is describing.
    ``daily_summary`` passing ``message=`` is a real instance of this.
    """

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        merged = dict(self.extra or {})
        merged.update(kwargs.get("extra") or {})  # call-site context wins
        kwargs["extra"] = {
            (f"{k}_" if k in _RECORD_ATTRS else k): v for k, v in merged.items()
        }
        return msg, kwargs


def get_logger(automation: str, run_id: str | None = None) -> logging.LoggerAdapter:
    """Return a logger bound to an automation name and a run id.

    The returned adapter injects ``automation`` and ``run_id`` into every record,
    and merges any per-call context: ``log.info("sent", extra={"client_id": "42"})``.
    """
    run_id = run_id or uuid.uuid4().hex[:12]
    logger = logging.getLogger(f"dror_barak.{automation}")
    if not logger.handlers:
        stream = sys.stdout
        # Logs and message copy are in Hebrew; the default Windows console
        # codepage (cp1252) can't encode them and would raise. Force UTF-8 on
        # the stream where supported, and never let an un-encodable character
        # crash a run.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):  # pragma: no cover - stream-dependent
                pass
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return _ContextAdapter(logger, {"automation": automation, "run_id": run_id})

"""Shared scaffolding for automations.

Provides:
  * :class:`Automation` — binds a logger + run-id and offers ``log_action`` that
    writes both a structured log line and a run-log entry (feeding the CRM log and
    the daily summary).
  * :func:`build_arg_parser` / :func:`run_cli` — a consistent CLI with ``--dry-run``.
"""

from __future__ import annotations

import argparse
import uuid
from typing import Any, Callable

from ..lib import config, run_log
from ..lib.logging_setup import get_logger


class Automation:
    def __init__(self, name: str, *, dry_run: bool = False, run_id: str | None = None):
        self.name = name
        self.dry_run = dry_run
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.log = get_logger(name, self.run_id)

    def log_action(
        self,
        action: str,
        status: str = "ok",
        *,
        client_id: str | None = None,
        detail: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Emit a structured log line and append a run-log entry."""
        self.log.info(
            action,
            extra={"status": status, "client_id": client_id, "detail": detail, **extra},
        )
        return run_log.record(
            self.name,
            action,
            status,
            client_id=client_id,
            dry_run=self.dry_run,
            detail=detail,
            run_id=self.run_id,
            **extra,
        )


def build_arg_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use mock clients; perform no network I/O or production side effects.",
    )
    return parser


def run_cli(
    parser: argparse.ArgumentParser,
    handler: Callable[[argparse.Namespace], Any],
) -> Any:
    """Parse args, load ``.env``, and invoke ``handler``."""
    config.load_dotenv()
    args = parser.parse_args()
    return handler(args)

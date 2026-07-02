"""Base class for API clients."""

from __future__ import annotations

from typing import Any

from .. import http


class BaseClient:
    """Common behaviour for all API clients.

    Subclasses implement live behaviour in methods and check ``self.dry_run``
    to short-circuit with a canned response (recording the intended call in
    ``self.calls`` so tests and dry-runs can assert what *would* have happened).
    """

    #: Human-readable system name, for logs and mock responses.
    system = "base"

    def __init__(self, *, dry_run: bool = False):
        self.dry_run = dry_run
        self.calls: list[dict[str, Any]] = []

    def _record(self, method: str, **fields: Any) -> dict[str, Any]:
        """Record an intended call (used in dry-run) and return it."""
        call = {"system": self.system, "method": method, **fields}
        self.calls.append(call)
        return call

    def _request(self, method: str, url: str, **kwargs: Any):
        """Live HTTP with retry/backoff. Never invoked in dry-run."""
        return http.request(method, url, **kwargs)

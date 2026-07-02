"""Anthropic (Claude) client — the AI behind the smart deliverables.

Powers the social-media prep report, the campaign recommendations, and the
strategy bot. Uses the Anthropic Messages API. Default model is ``claude-opus-4-8``
for the heavier strategy/analysis work; callers may pass ``claude-sonnet-5`` for
lighter tasks. Dry-run returns a canned, clearly-labelled completion so the
surrounding automation logic is testable without spending tokens.
"""

from __future__ import annotations

from typing import Any, Optional

from .. import config
from .base import BaseClient

DEFAULT_MODEL = "claude-opus-4-8"


class AnthropicClient(BaseClient):
    system = "anthropic"

    def __init__(self, *, dry_run: bool = False, model: str = DEFAULT_MODEL):
        super().__init__(dry_run=dry_run)
        self.model = model
        if not dry_run:
            self.base_url = config.get(
                "ANTHROPIC_BASE_URL", "https://api.anthropic.com"
            ).rstrip("/")
            self.api_key = config.require("ANTHROPIC_API_KEY")

    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: int = 2000,
    ) -> str:
        """Return the model's text response to a single prompt."""
        if self.dry_run:
            self._record(
                "complete", model=self.model, system=system, prompt=prompt[:200]
            )
            return (
                "[DRY-RUN AI OUTPUT] "
                "This is a placeholder completion. On a live run, Claude "
                f"({self.model}) would return generated content here based on the "
                "prompt."
            )
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        resp = self._request(
            "POST",
            f"{self.base_url}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        )
        data = resp.json()
        return "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )

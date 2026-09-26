"""Anthropic (Claude) client — the AI behind the smart deliverables.

Powers the social-media prep report, the campaign recommendations, and the
strategy bot, through the official ``anthropic`` SDK (which retries 429/5xx and
connection errors itself). Dry-run returns a canned, clearly-labelled completion
so the surrounding automation logic is testable without spending tokens.

``web=True`` gives Claude Anthropic's server-side web search and web fetch
tools, so it can actually read the pages it is asked about. Without them a
prompt that says "analyze this Instagram profile" gets an answer written from
the URL alone — plausible, specific and invented. Social platforms often refuse
automated fetches; the callers' prompts require saying so rather than guessing.
"""

from __future__ import annotations

from typing import Any, Optional

from .. import config, text_style
from .base import BaseClient

DEFAULT_MODEL = "claude-opus-4-8"

# Server-side tools: run on Anthropic's infrastructure, nothing to execute here.
# web_fetch can only open URLs already present in the conversation — which is
# exactly the profile links we put in the prompt.
WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 8},
]

# A long server-tool turn can stop with stop_reason "pause_turn"; re-sending the
# conversation resumes it. Capped so a misbehaving turn cannot loop forever.
MAX_CONTINUATIONS = 5


class AnthropicClient(BaseClient):
    system = "anthropic"

    def __init__(self, *, dry_run: bool = False, model: str = DEFAULT_MODEL):
        super().__init__(dry_run=dry_run)
        self.model = model
        self._sdk: Any = None
        if not dry_run:
            import anthropic

            self._sdk = anthropic.Anthropic(
                api_key=config.require("ANTHROPIC_API_KEY"),
                base_url=config.get("ANTHROPIC_BASE_URL") or None,
                max_retries=3,
                timeout=600.0,
            )

    def create_message(
        self,
        messages: list[dict[str, Any]],
        *,
        system: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        max_tokens: int = 16000,
    ) -> dict[str, Any]:
        """One Messages API call, the building block of a tool-use loop (the
        משימות agent, :mod:`src.automations.clickup_to_claude`).

        Returns the response as a plain dict. The caller appends ``content`` back
        into the conversation unchanged: with thinking on, the thinking blocks must
        be returned exactly as they came. Dry-run returns a finished text answer,
        so a loop built on this ends after one turn. Carries the house style like
        :meth:`complete`; the caller cleans the final text (:mod:`text_style`).
        """
        system = f"{system}\n\n{text_style.AI_STYLE}" if system else text_style.AI_STYLE
        if self.dry_run:
            self._record("create_message", model=self.model, system=system, turns=len(messages),
                         tools=[t["name"] for t in tools or []])
            return {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": (
                    "[DRY-RUN AI OUTPUT] On a live run Claude would do the task here, "
                    "using the Drive and Gmail tools when the task needs them.")}],
            }
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "system": system,
            # Opus 4.8 thinks only when asked; a task that plans tool calls benefits.
            "thinking": {"type": "adaptive"},
        }
        if tools:
            params["tools"] = tools
        response = self._sdk.messages.create(**params)
        _log_usage(self.model, response)
        return response.model_dump(mode="json", exclude_none=True)

    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: int = 2000,
        web: bool = False,
        thinking: bool = False,
    ) -> str:
        """Return the model's text response to a single prompt.

        ``web`` enables web search/fetch; ``thinking`` enables adaptive thinking
        for the heavier deliverables (a strategy, not a paragraph). Everything
        comes back in the house style (:mod:`text_style`): it is sent in Dror's name.
        """
        system = f"{system}\n\n{text_style.AI_STYLE}" if system else text_style.AI_STYLE
        if self.dry_run:
            self._record(
                "complete", model=self.model, system=system, prompt=prompt[:200],
                web=web, thinking=thinking,
            )
            return (
                "[DRY-RUN AI OUTPUT] "
                "This is a placeholder completion. On a live run, Claude "
                f"({self.model}) would return generated content here based on the "
                "prompt."
            )

        params: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens}
        if system:
            params["system"] = system
        if web:
            params["tools"] = WEB_TOOLS
        if thinking:
            params["thinking"] = {"type": "adaptive"}

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        response = self._sdk.messages.create(messages=messages, **params)
        _log_usage(self.model, response)
        for _ in range(MAX_CONTINUATIONS):
            if response.stop_reason != "pause_turn":
                break
            # Resume the server-side tool loop: send the paused turn back as is.
            messages = [{"role": "user", "content": prompt},
                        {"role": "assistant", "content": response.content}]
            response = self._sdk.messages.create(messages=messages, **params)

        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined this request (stop_reason=refusal)")
        if response.stop_reason == "max_tokens":
            # A truncated strategy or report must not reach Dror as if complete.
            raise RuntimeError(f"Claude's reply was cut off at max_tokens={max_tokens}")
        return text_style.humanize(final_text(response.content))


def _log_usage(model: str, response: Any) -> None:
    """One log line per call with its token counts: the basis for knowing what
    each automation costs before tuning any of them."""
    from ..logging_setup import get_logger

    usage = getattr(response, "usage", None)
    if usage is None:
        return
    get_logger("anthropic", "usage").info("claude_usage", extra={
        "model": model, "stop_reason": getattr(response, "stop_reason", None),
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
    })


_TOOL_BLOCKS = {"server_tool_use", "web_search_tool_result", "web_fetch_tool_result"}


def final_text(content: list[Any]) -> str:
    """The answer, without the narration Claude writes before using a tool
    ("I'll open that page for you…"): only text after the last tool block."""
    last_tool = max((i for i, b in enumerate(content) if b.type in _TOOL_BLOCKS), default=-1)
    return "".join(b.text for b in content[last_tool + 1:] if b.type == "text").strip()

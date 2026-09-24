"""Work that must not happen while someone waits on an HTTP response.

API Gateway gives a request 30 seconds. The social-media analysis (real web
fetches, one Claude call per profile), the strategy (research plus a long
document) and a campaign report all take longer — so a client who submits the
questionnaire, or a ClickUp button press, would get a timeout for work that
actually succeeded.

On Lambda, :func:`dispatch` re-invokes the running function asynchronously with
``{"task": name, "args": {...}}`` and returns at once; :func:`run` executes it
there. Off Lambda (CLI, tests, the local server) it simply runs inline.

A task that fails reports it where Dror looks — the run-log (and so the daily
email and dashboard) and, for a client, a comment on their ClickUp task —
because an async invocation's error goes nowhere else.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from .logging_setup import get_logger

log = get_logger("tasks", "dispatch")


def _registry() -> dict[str, tuple[str, Callable[..., Any]]]:
    """name -> (Hebrew label for failure messages, callable). Imported lazily."""
    from .. import sign_page
    from ..automations import campaign_summary, social_prep, strategy_bot

    return {
        "social_prep": ("דוח הכנה לרשתות", social_prep.run),
        "strategy_bot": ("בניית אסטרטגיה", strategy_bot.run),
        "campaign_summary": ("דוח קמפיין", campaign_summary.run),
        "file_contract": ("תיוק ההסכם החתום", sign_page.file_contract),
    }


def dispatch(name: str, *, dry_run: bool = False, **args: Any) -> dict[str, Any]:
    """Run ``name`` in the background when on Lambda, inline otherwise."""
    if name not in _registry():
        raise KeyError(f"unknown task {name!r}")
    function = os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    if not function:
        return {"inline": True, "result": run(name, dict(args), dry_run=dry_run)}
    import boto3

    boto3.client("lambda").invoke(
        FunctionName=function, InvocationType="Event",
        Payload=json.dumps({"task": name, "args": args, "dry_run": dry_run}).encode("utf-8"),
    )
    log.info("task_dispatched", extra={"task": name, "args": args})
    return {"queued": True, "task": name}


def run(name: str, args: dict[str, Any], *, dry_run: bool = False) -> Any:
    """Execute a task now, reporting a failure rather than just raising it."""
    label, fn = _registry()[name]
    try:
        return fn(**args, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - reported, then re-raised
        from ..automations.base import Automation

        client_id = args.get("client_id")
        Automation(name, dry_run=dry_run).log_action(
            "task_failed", "error", client_id=client_id, detail=f"{label}: {exc}")
        if client_id:
            try:
                from .clients.crm import CrmClient

                CrmClient(dry_run=False).append_automation_log(
                    str(client_id), f"❌ {label} נכשל: {exc}")
            except Exception:  # noqa: BLE001 - the run-log already has it
                pass
        raise

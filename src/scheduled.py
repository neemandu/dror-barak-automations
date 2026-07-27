"""EventBridge entrypoints for the scheduled automations.

Kept apart from lambda_handler (the HTTP/webhook entry): a scheduled invoke has no
request, no signature, no idempotency key — just "run the job". Mixing the two
entrypoints would blur which env and which guards each needs.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .lib import config


def reminders_handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """Daily: chase what clients still owe us — a signature, then a questionnaire.

    Both chases in one invoke: they run on the same daily cadence and each is a
    handful of API calls, so a second scheduled function would be a second thing
    to deploy, watch and pay for. They are independent — one failing must not
    silence the other, which is why the second is not simply appended after the
    first's return.
    """
    config.load_dotenv()
    from .automations import questionnaire_reminders, sign_reminders

    out: dict[str, Any] = {}
    for key, job in (("signatures", sign_reminders), ("questionnaires", questionnaire_reminders)):
        try:
            out[key] = job.run()
        except Exception as exc:  # noqa: BLE001
            out[key] = {"error": str(exc)}
            # Into the run-log too: a job that died whole logs nothing of its own,
            # and Lambda's return value is not somewhere Dror ever looks.
            from .automations.base import Automation

            Automation("reminders").log_action(
                f"{key}_job_failed", "error", detail=str(exc))
    return out


def campaign_report_handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """Monthly: one campaign report per active client.

    Two roles, one function. Invoked by the schedule with an empty event it is the
    **dispatcher**: it lists the active clients and async-invokes itself once per
    client, so each report gets its own Lambda and its own timeout budget — a loop
    in one invoke would blow the 15-minute ceiling long before it reached the last
    client. Invoked with ``{"client_id": ...}`` it is a **child**: it builds that
    one report.

    Off Lambda (no function name in the environment) there is nothing to fan out
    to, so it falls back to a sequential run — which is what the CLI and tests use.
    """
    config.load_dotenv()
    from .automations import campaign_summary

    event = event or {}
    client_id = event.get("client_id")
    if client_id:
        return campaign_summary.run(str(client_id), month=event.get("month"))

    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    if not function_name:
        return campaign_summary.run_all(month=event.get("month"))

    return _fan_out(function_name, month=event.get("month"))


def _fan_out(function_name: str, *, month: str | None) -> dict[str, Any]:
    """Async-invoke this function once per active client."""
    import boto3

    from .lib.clients.crm import CrmClient

    lam = boto3.client("lambda")
    clients = CrmClient().list_active_clients()
    for client in clients:
        payload = {"client_id": str(client.get("id") or "")}
        if month:
            payload["month"] = month
        lam.invoke(
            FunctionName=function_name,
            InvocationType="Event",  # async: fire and forget, one child per client
            Payload=json.dumps(payload).encode("utf-8"),
        )
    return {"dispatched": len(clients)}

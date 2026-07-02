"""Webhook receiver — maps inbound webhooks to automations.

A small, dependency-free (stdlib ``http.server``) receiver so the webhook-driven
automations can run without a heavyweight framework. Each route pulls the ids it
needs from the request JSON and calls the matching automation. Every automation is
also runnable directly (CLI / import), so this server is optional glue.

Routes:
  POST /crm/new-lead    -> T1 lead_to_contacts        (body: {"client_id"})
  POST /crm/status      -> T2/T5 by sub_status        (body: {"client_id","sub_status"})
  POST /forms/submit    -> T3 social_prep             (body: {"client_id"})
  POST /fillout/signed  -> T4 send_quote.signed       (body: {"client_id","submission_id"})
  POST /clickup/task    -> T9 clickup_to_claude       (body: {"task_id"})

Run:
    python -m src.webhook_server            # live
    python -m src.webhook_server --dry-run  # dispatch automations in dry-run

Configure the port via ``WEBHOOK_PORT`` (default 8000). Put this behind a proper
reverse proxy / auth before exposing it publicly.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

from .lib import config
from .lib.clients.crm import SUB_INITIAL_MEETING, SUB_SIGNED

DRY_RUN = False


def _dispatch(path: str, body: dict[str, Any]) -> dict[str, Any]:
    # Imported lazily so importing this module stays cheap and side-effect-free.
    from .automations import (
        clickup_to_claude,
        lead_to_contacts,
        onboarding,
        send_questionnaire,
        send_quote,
        social_prep,
    )

    if path == "/crm/new-lead":
        return lead_to_contacts.run(body["client_id"], dry_run=DRY_RUN)
    if path == "/crm/status":
        sub = body.get("sub_status")
        if sub == SUB_INITIAL_MEETING:
            return send_questionnaire.run(body["client_id"], dry_run=DRY_RUN)
        if sub == SUB_SIGNED:
            return onboarding.run(body["client_id"], dry_run=DRY_RUN)
        return {"ignored": f"no automation for sub_status={sub}"}
    if path == "/forms/submit":
        return social_prep.run(body["client_id"], dry_run=DRY_RUN)
    if path == "/fillout/signed":
        return send_quote.signed(
            body["client_id"], body["submission_id"], dry_run=DRY_RUN
        )
    if path == "/clickup/task":
        return clickup_to_claude.run(body["task_id"], dry_run=DRY_RUN)
    raise KeyError(f"no route for {path}")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
            result = _dispatch(self.path, body)
            self._respond(200, {"ok": True, "result": _safe(result)})
        except KeyError as exc:
            self._respond(404, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - report, don't crash the server
            self._respond(500, {"ok": False, "error": str(exc)})

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        pass


def _safe(result: Any) -> Any:
    """Best-effort JSON-serializable view of an automation's return value."""
    try:
        json.dumps(result)
        return result
    except TypeError:
        return {"summary": str(result)[:500]}


def serve(port: int, dry_run: bool) -> None:
    global DRY_RUN
    DRY_RUN = dry_run
    config.load_dotenv()
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(
        json.dumps(
            {"msg": "webhook server listening", "port": port, "dry_run": dry_run}
        )
    )
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Webhook receiver for automations")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    config.load_dotenv()
    port = args.port or int(config.get("WEBHOOK_PORT", "8000"))
    serve(port, args.dry_run)


if __name__ == "__main__":
    main()

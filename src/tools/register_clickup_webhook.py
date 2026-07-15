"""Register (and inspect) the ClickUp webhook that drives the automations.

ClickUp pushes task events to an endpoint; this creates that registration and
prints the **secret** ClickUp generates, which the Lambda needs in order to verify
that inbound traffic is genuinely from ClickUp.

Usage:
    # See what's registered today (read-only):
    python -m src.tools.register_clickup_webhook --list

    # Show what would be created, no writes:
    python -m src.tools.register_clickup_webhook --endpoint https://xyz.execute-api...
        --dry-run

    # Create it:
    python -m src.tools.register_clickup_webhook --endpoint https://xyz.execute-api...

    # Remove one:
    python -m src.tools.register_clickup_webhook --delete <webhook_id>

Then put the printed secret in the stack:
    CLICKUP_WEBHOOK_SECRET=<secret>

ClickUp disables a webhook after repeated delivery failures — `--list` shows the
health, so that shows up here rather than as automations mysteriously not firing.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..lib import config
from ..lib.http import request as http_request

NAME = "register_clickup_webhook"

# taskCreated  -> a new lead: save the phone to Google Contacts.
# taskUpdated  -> covers custom-field changes, which is where `סטטוס` lives, so
#                 this is what actually fires the questionnaire and onboarding.
# taskStatusUpdated -> the task status (the primary lifecycle).
EVENTS = ["taskCreated", "taskUpdated", "taskStatusUpdated"]


def _headers() -> dict[str, str]:
    return {"Authorization": config.require("CLICKUP_API_TOKEN")}


def _base() -> str:
    return config.get("CLICKUP_BASE_URL", "https://api.clickup.com/api/v2").rstrip("/")


def list_webhooks(team_id: str) -> list[dict[str, Any]]:
    resp = http_request("GET", f"{_base()}/team/{team_id}/webhook", headers=_headers())
    return resp.json().get("webhooks", [])


def show(team_id: str) -> None:
    hooks = list_webhooks(team_id)
    if not hooks:
        print("No webhooks registered.")
        return
    for h in hooks:
        health = h.get("health") or {}
        print(f"\n  id:       {h.get('id')}")
        print(f"  endpoint: {h.get('endpoint')}")
        print(f"  events:   {h.get('events')}")
        print(f"  list_id:  {h.get('list_id')}")
        print(f"  health:   {health.get('status')} (fails: {health.get('fail_count')})")
        if str(health.get("status")) == "failing":
            print("            ^ ClickUp disables failing webhooks. Fix the endpoint,")
            print("              then re-register — automations are not firing.")


def create(team_id: str, endpoint: str, list_id: str | None, dry_run: bool) -> None:
    body: dict[str, Any] = {"endpoint": endpoint, "events": EVENTS}
    if list_id:
        # Scope to the clients list so unrelated task noise doesn't invoke Lambda.
        body["list_id"] = list_id

    if dry_run:
        print("Would POST to ClickUp (no writes):")
        print(f"  {_base()}/team/{team_id}/webhook")
        print(json.dumps(body, indent=2))
        print("\nClickUp would return a `secret` — that goes in CLICKUP_WEBHOOK_SECRET.")
        return

    resp = http_request(
        "POST", f"{_base()}/team/{team_id}/webhook", headers=_headers(), json=body
    )
    data = resp.json()
    hook = data.get("webhook") or data
    print("Webhook created.\n")
    print(f"  id:       {hook.get('id')}")
    print(f"  endpoint: {hook.get('endpoint')}")
    print(f"  events:   {hook.get('events')}")
    print("\n  Put this in .env and in the Lambda's CLICKUP_WEBHOOK_SECRET:\n")
    print(f"  CLICKUP_WEBHOOK_SECRET={hook.get('secret')}")
    print("\n  Without it the Lambda rejects every delivery as unsigned.")


def delete(webhook_id: str) -> None:
    http_request("DELETE", f"{_base()}/webhook/{webhook_id}", headers=_headers())
    print(f"Deleted webhook {webhook_id}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Register the ClickUp webhook")
    parser.add_argument("--list", action="store_true", help="Show registered webhooks.")
    parser.add_argument("--endpoint", help="Public URL to receive events.")
    parser.add_argument("--list-id", help="Scope to a list (default: CLICKUP_LIST_ID).")
    parser.add_argument("--delete", metavar="WEBHOOK_ID", help="Delete a webhook.")
    parser.add_argument("--dry-run", action="store_true", help="No writes; print the plan.")
    args = parser.parse_args()
    config.load_dotenv()

    team_id = config.get("CLICKUP_TEAM_ID")
    if not team_id:
        print("CLICKUP_TEAM_ID is not set. Find it with:\n"
              "  python -m src.tools.check_clickup_crm --discover")
        sys.exit(1)

    if args.list:
        show(str(team_id))
        return
    if args.delete:
        delete(args.delete)
        return
    if not args.endpoint:
        print("Nothing to do. Pass --endpoint <url>, --list, or --delete <id>.")
        sys.exit(1)

    if not str(args.endpoint).startswith("https://"):
        # ClickUp will not deliver to plain http, and a typo here looks like
        # "the automations don't fire" hours later.
        print(f"Endpoint must be https. Got: {args.endpoint}")
        sys.exit(1)

    create(str(team_id), args.endpoint, args.list_id or config.get("CLICKUP_LIST_ID"),
           args.dry_run)


if __name__ == "__main__":
    main()

"""Check that the ClickUp clients list is set up for the automations.

Custom fields cannot be created through ClickUp's API, so the list is built by
hand in the UI once. This tool reports what is present and what is missing, so
setup is a checklist rather than a guessing game.

Read-only: it never writes to ClickUp.

Usage:
    # List every list in the workspace, with its id (find the clients list):
    python -m src.tools.check_clickup_crm --discover

    # Check a specific list (defaults to CLICKUP_LIST_ID):
    python -m src.tools.check_clickup_crm --list-id 901819505305

Exit code is 0 when the list is usable, 1 when a required field or status is
missing — so it can gate a deploy.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from ..lib import config, crm_fields
from ..lib.clients.crm import OPTIONAL_FIELDS, REQUIRED_FIELDS
from ..lib.http import request as http_request

NAME = "check_clickup_crm"

OK = "  [ok]  "
MISS = "  [--]  "
BAD = "  [!!]  "


def _api(path: str, token: str) -> dict[str, Any]:
    base = config.get("CLICKUP_BASE_URL", "https://api.clickup.com/api/v2").rstrip("/")
    resp = http_request("GET", f"{base}{path}", headers={"Authorization": token})
    return resp.json()


def discover(token: str) -> None:
    """Print every list in the workspace so the clients list can be identified."""
    teams = _api("/team", token).get("teams", [])
    if not teams:
        print("No workspace found for this token.")
        return
    for team in teams:
        print(f"\nworkspace {team['id']}: {team['name']}")
        for space in _api(f"/team/{team['id']}/space?archived=false", token).get(
            "spaces", []
        ):
            print(f"  space {space['id']}: {space['name']}")
            for folder in _api(f"/space/{space['id']}/folder?archived=false", token).get(
                "folders", []
            ):
                print(f"    folder {folder['id']}: {folder['name']}")
                for lst in folder.get("lists", []):
                    print(f"      list {lst['id']}: {lst['name']} "
                          f"({lst.get('task_count')} tasks)   <- CLICKUP_LIST_ID")
            for lst in _api(f"/space/{space['id']}/list?archived=false", token).get(
                "lists", []
            ):
                print(f"    list {lst['id']}: {lst['name']} "
                      f"({lst.get('task_count')} tasks)   <- CLICKUP_LIST_ID")
    print("\nPut the id of the clients list in .env as CLICKUP_LIST_ID.")


def check(list_id: str, token: str) -> bool:
    """Report on a list. Returns True when it is usable."""
    meta = _api(f"/list/{list_id}", token)
    if "name" not in meta:
        print(f"{BAD}list {list_id} not found, or the token cannot see it.")
        return False

    print(f"\nlist {list_id}: {meta.get('name')}")
    ok = True

    print("\nstatuses (the primary status — lead/active/paused/finished):")
    names = [s.get("status", "") for s in meta.get("statuses", [])]
    found = {crm_fields.canonical_status(n): n for n in names if crm_fields.canonical_status(n)}
    for canonical in (crm_fields.STATUS_LEAD, crm_fields.STATUS_ACTIVE,
                      crm_fields.STATUS_PAUSED, crm_fields.STATUS_FINISHED):
        if canonical in found:
            print(f"{OK}{canonical:9} -> {found[canonical]!r}")
        else:
            # Only 'active' is load-bearing: the monthly billing run selects on it.
            required = canonical == crm_fields.STATUS_ACTIVE
            print(f"{BAD if required else MISS}{canonical:9} -> no matching status"
                  f"{'   (REQUIRED — the monthly billing run selects on it)' if required else ''}")
            ok = ok and not required
    unmapped = [n for n in names if not crm_fields.canonical_status(n)]
    if unmapped:
        print(f"       (not recognised, ignored: {unmapped})")

    fields = _api(f"/list/{list_id}/field", token).get("fields", [])
    resolved = crm_fields.resolve_fields(fields)

    print("\nrequired custom fields:")
    for canonical in REQUIRED_FIELDS:
        field = resolved.get(canonical)
        if field:
            print(f"{OK}{canonical:18} -> {field['name']!r} ({field['type']})")
        else:
            print(f"{BAD}{canonical:18} -> MISSING. Name it one of: "
                  f"{crm_fields.ALIASES[canonical][:3]}")
            ok = False

    print("\noptional custom fields:")
    for canonical in OPTIONAL_FIELDS:
        field = resolved.get(canonical)
        print(f"{OK}{canonical:18} -> {field['name']!r} ({field['type']})" if field
              else f"{MISS}{canonical:18} -> not set up "
                   f"(automations writing it will report it as skipped)")

    sub = resolved.get("sub_status")
    if sub:
        print("\nsecondary-status dropdown options:")
        if str(sub.get("type")) != "drop_down":
            print(f"{BAD}'{sub['name']}' is a {sub['type']}, expected a dropdown.")
            ok = False
        else:
            options = (sub.get("type_config") or {}).get("options") or []
            by_canonical = {
                crm_fields.canonical_sub_status(str(o.get("name"))): o.get("name")
                for o in options
            }
            for canonical in (crm_fields.SUB_INITIAL_MEETING,
                              crm_fields.SUB_QUESTIONNAIRE_SENT,
                              crm_fields.SUB_QUOTE_SENT, crm_fields.SUB_SIGNED,
                              crm_fields.SUB_IN_WORK):
                print(f"{OK}{canonical:20} -> {by_canonical[canonical]!r}" if canonical in by_canonical
                      else f"{MISS}{canonical:20} -> no matching option. Name it one of: "
                           f"{crm_fields.SUB_STATUS_ALIASES[canonical][:2]}")

    unknown = [f["name"] for f in fields if not crm_fields.canonical_for(f.get("name", ""))]
    if unknown:
        print(f"\nDror's own fields, left alone: {unknown}")

    print("\n" + ("READY — the automations can use this list."
                  if ok else "NOT READY — fix the [!!] lines above. See docs/CLICKUP_SETUP.md."))
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the ClickUp CRM list setup")
    parser.add_argument("--discover", action="store_true",
                        help="List every list in the workspace with its id.")
    parser.add_argument("--list-id", help="List to check (default: CLICKUP_LIST_ID).")
    args = parser.parse_args()
    config.load_dotenv()
    token = config.require("CLICKUP_API_TOKEN")

    if args.discover:
        discover(token)
        return

    list_id = args.list_id or config.get("CLICKUP_LIST_ID")
    if not list_id:
        print("No list to check. Pass --list-id, or set CLICKUP_LIST_ID in .env.\n"
              "Run with --discover to see the lists in the workspace.")
        sys.exit(1)
    sys.exit(0 if check(str(list_id), token) else 1)


if __name__ == "__main__":
    main()

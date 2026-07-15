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


def plan_report(token: str) -> bool:
    """Report the plan and the custom-field headroom. True when unconstrained.

    Free Forever caps the whole workspace at 60 Custom Field *uses*, counted each
    time a value is set on a task's field and never reset. The CRM puts ~10 fields
    on a client, so Free runs out at roughly 6 clients — worth knowing before the
    setup rather than when writes start failing.
    """
    teams = _api("/team", token).get("teams", [])
    if not teams:
        print(f"{BAD}no workspace visible to this token.")
        return False
    ok = True
    for team in teams:
        plan = _api(f"/team/{team['id']}/plan", token)
        name = str(plan.get("plan_name", "?"))
        seats = _api(f"/team/{team['id']}/seats", token).get("members", {})
        print(f"\nworkspace {team['id']}: {team['name']}")
        print(f"  plan:  {name}")
        print(f"  seats: {seats.get('filled_members_seats')}/{seats.get('total_member_seats')} members")
        if "free" in name.casefold():
            ok = False
            print(f"{BAD}Free Forever caps the WORKSPACE at 60 Custom Field uses.")
            print("       A 'use' = one value set on one task's custom field, and")
            print("       uses accumulate across the workspace and never reset.")
            print("       The CRM puts ~10 fields on a client -> ~6 clients, total.")
            print("       The automations then cannot write Drive links, contract")
            print("       links or Morning status at all.")
            print("       -> Upgrade, or use Plan B in docs/CLICKUP_SETUP.md.")
        else:
            print(f"{OK}paid plan — Custom Field uses are unlimited.")
    return ok


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

    fields = _api(f"/list/{list_id}/field", token).get("fields", [])
    resolved = crm_fields.resolve_fields(fields)
    status_field = resolved.get("status")

    print("\nprimary status (lead/active/paused/finished):")
    if status_field:
        # Supported, but not free: a dropdown burns a Custom Field use per client,
        # and ClickUp's board/automation features key off task statuses.
        print(f"{OK}held in the custom field {status_field['name']!r} "
              f"({status_field['type']}), not the task status.")
        options = (status_field.get("type_config") or {}).get("options") or []
        by_canonical = {
            crm_fields.canonical_status(str(o.get("name"))): o.get("name") for o in options
        }
        for canonical in (crm_fields.STATUS_LEAD, crm_fields.STATUS_ACTIVE,
                          crm_fields.STATUS_PAUSED, crm_fields.STATUS_FINISHED):
            if canonical in by_canonical:
                print(f"{OK}{canonical:9} -> {by_canonical[canonical]!r}")
            else:
                required = canonical == crm_fields.STATUS_ACTIVE
                print(f"{BAD if required else MISS}{canonical:9} -> no matching option"
                      f"{'   (REQUIRED — the monthly billing run selects on it)' if required else ''}")
                ok = ok and not required
        print(f"{MISS}note: as a field this costs one Custom Field use per client, and")
        print("       ClickUp cannot show the pipeline as a board or trigger on it.")
        print("       Task statuses are free and native — see docs/CLICKUP_SETUP.md.")
    else:
        names = [s.get("status", "") for s in meta.get("statuses", [])]
        found = {crm_fields.canonical_status(n): n for n in names if crm_fields.canonical_status(n)}
        for canonical in (crm_fields.STATUS_LEAD, crm_fields.STATUS_ACTIVE,
                          crm_fields.STATUS_PAUSED, crm_fields.STATUS_FINISHED):
            if canonical in found:
                print(f"{OK}{canonical:9} -> status {found[canonical]!r}")
            else:
                required = canonical == crm_fields.STATUS_ACTIVE
                print(f"{BAD if required else MISS}{canonical:9} -> no matching status"
                      f"{'   (REQUIRED — the monthly billing run selects on it)' if required else ''}")
                ok = ok and not required
        unmapped = [n for n in names if not crm_fields.canonical_status(n)]
        if unmapped:
            print(f"       (not recognised, ignored: {unmapped})")

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
        if not field:
            print(f"{MISS}{canonical:18} -> not set up "
                  f"(automations writing it will report it as skipped)")
            continue
        print(f"{OK}{canonical:18} -> {field['name']!r} ({field['type']})")
        if str(field.get("type")) == "attachment":
            # The automations store a link; an attachment field wants a file.
            print(f"{BAD}{'':18}    ^ Attachment fields take an uploaded file, but the "
                  f"automations write a link.\n{'':26}Change {field['name']!r} to type "
                  f"URL, or it will never be written.")
            ok = False

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
    parser.add_argument("--plan", action="store_true",
                        help="Report the plan tier and the custom-field cap.")
    parser.add_argument("--list-id", help="List to check (default: CLICKUP_LIST_ID).")
    args = parser.parse_args()
    config.load_dotenv()
    token = config.require("CLICKUP_API_TOKEN")

    if args.discover:
        discover(token)
        return
    if args.plan:
        sys.exit(0 if plan_report(token) else 1)

    list_id = args.list_id or config.get("CLICKUP_LIST_ID")
    if not list_id:
        print("No list to check. Pass --list-id, or set CLICKUP_LIST_ID in .env.\n"
              "Run with --discover to see the lists in the workspace.")
        sys.exit(1)

    # The plan gates everything else: a perfectly configured list still cannot be
    # written to once the workspace is out of Custom Field uses.
    plan_ok = plan_report(token)
    list_ok = check(str(list_id), token)

    tasks_list = config.get("CLICKUP_TASKS_LIST_ID")
    if tasks_list:
        meta = _api(f"/list/{tasks_list}", token)
        print(f"\ntasks list {tasks_list}: {meta.get('name', '?')}")
        fields = _api(f"/list/{tasks_list}/field", token).get("fields", [])
        rel = [f for f in fields if str(f.get("type")) in ("list_relationship", "tasks")]
        print(f"{OK}'{rel[0]['name']}' links work tasks to clients." if rel
              else f"{MISS}no Relationship field — work tasks cannot point at a client. "
                   f"See docs/CLICKUP_SETUP.md step 2.")
    else:
        print(f"\n{MISS}CLICKUP_TASKS_LIST_ID not set — per-client tasks not configured.")

    sys.exit(0 if (plan_ok and list_ok) else 1)


if __name__ == "__main__":
    main()

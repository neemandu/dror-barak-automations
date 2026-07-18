"""Assign newly-shared Meta ad accounts to the system user — so the report can read them.

When a client shares their ad account with Dror's business (Partner access), it
becomes *reachable* by the business but is not yet *assigned* to the system user —
and until it is, the token 403s on it (see docs/OPERATIONS.md §5). That assignment
is the one manual step in onboarding a client's campaigns; this tool does it via
the API instead.

It lists every account the business can reach (partner-shared + owned), subtracts
the ones already assigned to the system user, and assigns the rest with ANALYZE
(read) access. With ``--write-clickup`` it also fills the ``Meta`` field on a
matching ClickUp client, so you never paste an ``act_`` id by hand either.

Needs a token with **`business_management`** (on top of `ads_read`) and
``META_BUSINESS_ID`` in ``.env`` (Dror's portfolio — Business Settings → Business
Info). The system user is read from the token itself.

**Two limits found in testing, so expectations are honest:**

  * **Assigning needs an ADMIN system user.** An *Employee* system user (the
    default) can read the business but not create assignments — the assign call is
    refused. Make ``Automation`` an Admin and regenerate its token for the assign
    step to work. The detection and ClickUp fill work either way.
  * **ClickUp fill matches by name**, and a Meta account name rarely equals the
    ClickUp client name — so it only fills the clear matches and leaves the rest.
    Its real value is *detection*: telling you which reachable accounts aren't
    assigned yet, so you're never guessing what's missing.

Usage:
    python -m src.tools.sync_meta_accounts --dry-run          # preview only
    python -m src.tools.sync_meta_accounts                    # assign
    python -m src.tools.sync_meta_accounts --write-clickup    # assign + fill ClickUp

Exit code is 0 on success, 1 on a hard failure (bad token, missing config).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from ..lib import config, crm_fields
from ..lib.clients.crm import CrmClient
from ..lib.clients.meta_ads import MetaAdsClient, MetaError

NAME = "sync_meta_accounts"


def accounts_to_assign(
    reachable: list[dict[str, str]], assigned_ids: set[str]
) -> list[dict[str, str]]:
    """The reachable accounts not yet assigned to the system user. Pure — testable."""
    return [a for a in reachable if a["account_id"] not in assigned_ids]


def _fill_clickup(reachable: list[dict[str, str]], *, dry_run: bool) -> int:
    """Fill the ``Meta`` field on clients whose name matches a reachable account.

    Conservative: only writes when the field is empty and exactly one account name
    matches the client name (normalised). An ambiguous or missing match is left for
    a human — a wrong account id in the field is worse than an empty one.
    """
    crm = CrmClient()
    by_name: dict[str, list[dict[str, str]]] = {}
    for acct in reachable:
        by_name.setdefault(crm_fields.normalize(acct["name"]), []).append(acct)

    filled = 0
    for client in crm.list_active_clients():
        if str(client.get("meta_ad_account") or "").strip():
            continue  # already set — never overwrite
        matches = by_name.get(crm_fields.normalize(str(client.get("name") or "")), [])
        if len(matches) != 1:
            if matches:
                print(f"  [skip] {client.get('name')!r}: {len(matches)} accounts match by name")
            continue
        act = f"act_{matches[0]['account_id']}"
        if dry_run:
            print(f"  [dry-run] would set {client.get('name')!r} → {act}")
        else:
            crm.update_fields(str(client["id"]), meta_ad_account=act)
            print(f"  set {client.get('name')!r} → {act}")
        filled += 1
    return filled


def run(*, dry_run: bool = False, write_clickup: bool = False, business_id: str | None = None) -> dict[str, Any]:
    meta = MetaAdsClient()
    business_id = business_id or config.require("META_BUSINESS_ID")

    su = meta.me()
    assigned = meta.assigned_account_ids()
    reachable = meta.reachable_accounts(business_id)
    todo = accounts_to_assign(reachable, assigned)

    print(f"system user : {su['name']} ({su['id']})")
    print(f"business    : {business_id}")
    print(f"reachable={len(reachable)}  already assigned={len(assigned)}  to assign={len(todo)}")

    assigned_now = 0
    hinted = False
    for acct in todo:
        act = f"act_{acct['account_id']}"
        if dry_run:
            print(f"  [dry-run] would assign {act} — {acct['name']}")
            continue
        try:
            meta.assign_account(acct["account_id"], su["id"])
            print(f"  assigned {act} — {acct['name']}")
            assigned_now += 1
        except Exception as exc:  # noqa: BLE001 - one bad account must not stop the rest
            print(f"  [error] {act}: {exc}")
            # Assigning assets needs an ADMIN system user; an EMPLOYEE token can
            # read the business but not write assignments. Say so once, plainly.
            if not hinted and ("missing permissions" in str(exc) or "Unsupported post" in str(exc)):
                print("  → assignment was refused. Make the 'Automation' system user an "
                      "ADMIN (Business Settings → System Users → role → Admin) and "
                      "regenerate its token, or assign these in the UI. Detection and "
                      "ClickUp fill below still work regardless.")
                hinted = True

    filled = _fill_clickup(reachable, dry_run=dry_run) if write_clickup else 0
    return {"reachable": len(reachable), "to_assign": len(todo),
            "assigned": assigned_now, "clickup_filled": filled}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__ or NAME)
    parser.add_argument("--dry-run", action="store_true", help="Preview; assign nothing")
    parser.add_argument("--write-clickup", action="store_true",
                        help="Also fill the Meta field on matching ClickUp clients")
    parser.add_argument("--business", help="Business portfolio id (default: META_BUSINESS_ID)")
    args = parser.parse_args()
    config.load_dotenv()
    try:
        run(dry_run=args.dry_run, write_clickup=args.write_clickup, business_id=args.business)
    except (MetaError, KeyError) as exc:
        print(f"[!!] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()

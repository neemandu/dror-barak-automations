"""Fill ``חוזה בדשבורד`` on every client task that has no link yet.

New clients get it from the webhook (src/lib/contract_link.py); this is for the
ones that existed before. Needs DASHBOARD_URL (the stack's DashboardUrl output) in
.env, and the column on the list (``python -m src.tools.setup_clickup_fields --apply
--only dashboard_contract``).

Usage:
    python -m src.tools.fill_contract_links            # show what would be written
    python -m src.tools.fill_contract_links --apply    # write it
"""

from __future__ import annotations

import argparse

from ..lib import config, contract_link
from ..lib.clients.crm import CrmClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill the contract-page link on client tasks")
    parser.add_argument("--apply", action="store_true", help="write the links (default: only show)")
    args = parser.parse_args()
    config.load_dotenv()
    if not contract_link.url_for("x"):
        raise SystemExit("DASHBOARD_URL is not set: copy the stack's DashboardUrl output into .env")
    crm = CrmClient()
    if "dashboard_contract" not in crm.fields():
        raise SystemExit("the list has no 'חוזה בדשבורד' column: "
                         "python -m src.tools.setup_clickup_fields --apply --only dashboard_contract")
    for client in crm._all_clients():
        cid, name = str(client.get("id")), str(client.get("name") or "")
        if client.get("dashboard_contract"):
            continue
        url = contract_link.url_for(cid)
        if not args.apply:
            print(f"would set  {name}  ->  {url}")
            continue
        crm.update_fields(cid, dashboard_contract=url)
        print(f"set  {name}")
    if not args.apply:
        print("\nrun again with --apply to write them.")


if __name__ == "__main__":
    main()

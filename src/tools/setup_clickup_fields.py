"""Create the clients list's optional columns that are missing, through ClickUp's API.

ClickUp's reference lists no endpoint that creates a custom field, but
``POST /api/v2/list/{list_id}/field`` with ``{"name", "type"}`` does: the six
document and date columns were made with it on 2026-09-27. Idempotent: a column the
list already has (under any name the code recognises) is left alone. Buttons still
need the UI, because their Automation (call a webhook) cannot be made through the API.

Usage:
    python -m src.tools.setup_clickup_fields                 # show what is missing
    python -m src.tools.setup_clickup_fields --apply         # create it
    python -m src.tools.setup_clickup_fields --apply --only price_strategy price_campaigns
"""

from __future__ import annotations

import argparse
from typing import Any

from ..lib import config, crm_fields
from ..lib.http import request as http_request

#: canonical field -> (the name it gets, ClickUp type, type_config)
SPECS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "questionnaire_answers": ("תשובות שאלון", "attachment", {}),
    "strategy_pdf": ("אסטרטגיה", "attachment", {}),
    "campaign_report_pdf": ("דוח קמפיין", "attachment", {}),
    "social_report_pdf": ("דוח רשתות", "attachment", {}),
    "quote_sent_at": ("חוזה נשלח", "date", {}),
    "questionnaire_sent_at": ("שאלון נשלח", "date", {}),
    "price_strategy": ("מחיר אסטרטגיה", "currency", {"currency_type": "ILS", "precision": 0}),
    "price_campaigns": ("מחיר קמפיינים", "currency", {"currency_type": "ILS", "precision": 0}),
    "service_type": ("סוג שירות", "short_text", {}),
    "recordings_path": ("נתיב הקלטות", "short_text", {}),
}

BASE = "https://api.clickup.com/api/v2"


def missing(fields: list[dict[str, Any]], only: list[str] | None = None) -> list[str]:
    """The canonical columns in ``SPECS`` the list does not have yet."""
    have = crm_fields.resolve_fields(fields)
    return [key for key in SPECS if key not in have and (not only or key in only)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create missing ClickUp columns")
    parser.add_argument("--apply", action="store_true", help="create them (default: only show)")
    parser.add_argument("--only", nargs="*", help="limit to these canonical names")
    parser.add_argument("--list-id", help="default: CLICKUP_LIST_ID")
    args = parser.parse_args()

    config.load_dotenv()
    headers = {"Authorization": config.require("CLICKUP_API_TOKEN")}
    list_id = args.list_id or config.require("CLICKUP_LIST_ID")
    fields = http_request("GET", f"{BASE}/list/{list_id}/field", headers=headers).json().get("fields", [])
    todo = missing(fields, args.only)
    if not todo:
        print("nothing to create - every column is there.")
        return
    for key in todo:
        name, ftype, type_config = SPECS[key]
        if not args.apply:
            print(f"would create  {name!r} ({ftype})  for {key}")
            continue
        body: dict[str, Any] = {"name": name, "type": ftype}
        if type_config:
            body["type_config"] = type_config
        made = http_request("POST", f"{BASE}/list/{list_id}/field", headers=headers, json=body).json()
        print(f"created  {name!r} ({ftype})  id={(made.get('field') or {}).get('id')}")
    if not args.apply:
        print("\nrun again with --apply to create them.")


if __name__ == "__main__":
    main()

"""Give the leads logged before 2026-09-24 their name and their list's name.

Until then ``smoove_to_manychat`` logged a lead by phone only, and a list by its
Smoove number, so the dashboard's לידים tab showed 115 "ללא שם" rows across
"רשימה 889128" and "רשימה 1142673". ManyChat knows both: the contact's name
(``getInfo``) and the Flow's name for each list (``getFlows``).

Runs where the ManyChat key is, inside the Smoove Lambda (secrets stay on the
stack), by a direct invoke:

    {"task": "backfill_leads"}                 # report what it would change
    {"task": "backfill_leads", "apply": true}  # write it

It only adds ``name`` / ``list_name`` to entries that lack them; nothing else in
an entry changes, and running it twice changes nothing the second time.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from ..automations.smoove_to_manychat import flow_env_key
from ..lib import config
from ..lib.clients.manychat import ManyChatClient


def _table() -> Any:
    import boto3

    return boto3.resource("dynamodb", region_name=config.get("AWS_REGION", "eu-central-1")).Table(
        config.require("RUN_LOG_TABLE"))


def _lead_items(table: Any) -> list[dict[str, Any]]:
    from boto3.dynamodb.conditions import Attr

    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"FilterExpression": Attr("automation").eq("smoove_to_manychat")}
    while True:
        resp = table.scan(**kwargs)
        items += resp.get("Items", [])
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def run(*, apply: bool = False, table: Any = None, mc: Optional[ManyChatClient] = None,
        pause: float = 0.12) -> dict[str, Any]:
    """Report (and with ``apply``, write) the names ManyChat has for logged leads."""
    table = table or _table()
    mc = mc or ManyChatClient()
    flows = mc.flow_names()
    items = _lead_items(table)
    names: dict[str, str] = {}
    lists: dict[str, str] = {}
    stats = {"entries": len(items), "named_now": 0, "no_name_in_manychat": 0, "had_name": 0, "updated": 0}
    for item in items:
        sets: dict[str, str] = {}
        if item.get("name"):
            stats["had_name"] += 1
        elif item.get("subscriber_id"):
            sid = str(item["subscriber_id"])
            if sid not in names:
                names[sid] = mc.subscriber_name(sid)
                time.sleep(pause)  # ManyChat rate-limits getInfo
            if names[sid]:
                sets["name"] = names[sid]
                stats["named_now"] += 1
            else:
                stats["no_name_in_manychat"] += 1
        msg = str(item.get("msg") or "")
        if msg:
            list_name = flows.get(config.get(flow_env_key(msg)) or "", "")
            if list_name:
                lists[msg] = list_name
                if not item.get("list_name"):
                    sets["list_name"] = list_name
        if sets:
            stats["updated"] += 1
            if apply:
                table.update_item(
                    Key={"day": item["day"], "ts_id": item["ts_id"]},
                    UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in sets),
                    ExpressionAttributeNames={f"#{k}": k for k in sets},
                    ExpressionAttributeValues={f":{k}": v for k, v in sets.items()},
                )
    return {**stats, "lists": lists, "applied": apply}

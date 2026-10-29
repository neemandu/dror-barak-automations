"""The link from a client's ClickUp task to their contract page on the dashboard.

A ClickUp button can only run an automation, never open a page, so the way from
the task to "edit this contract before sending" is a URL column, ``חוזה בדשבורד``,
filled in once per client by the webhook (on creation or any later update) and
for existing clients by ``python -m src.tools.fill_contract_links``. A list
without the column skips it quietly, like every optional column.
"""

from __future__ import annotations

from urllib.parse import quote

from . import config, idempotency

#: Once written, a client's link never needs writing again (its id does not change).
ONCE_S = 10 * 365 * 24 * 3600
#: Without the column (or without the dashboard's address) try again after a day.
RETRY_S = 24 * 3600


def url_for(client_id: str) -> str:
    """The client's contract page, or "" when the dashboard's address is not known."""
    base = str(config.get("DASHBOARD_URL") or "").rstrip("/")
    if not base:
        return ""
    if base.endswith("/dashboard"):
        base = base[: -len("/dashboard")]
    return f"{base}/contracts/{quote(str(client_id), safe='')}"


def ensure(client_id: str, *, dry_run: bool = False, crm=None) -> bool:
    """Write the link on the task once. True when it was written now."""
    url = url_for(client_id)
    if not url:
        return False
    once = idempotency.guard("contract_link", client_id)
    if not idempotency.claim(once, ttl=ONCE_S):
        return False
    try:
        if crm is None:
            from .clients.crm import CrmClient

            crm = CrmClient(dry_run=dry_run)
        result = crm.update_fields(client_id, dashboard_contract=url)
    except Exception:
        idempotency.release(once)
        raise
    if "dashboard_contract" in (result.get("skipped") or {}):
        idempotency.release(once)  # no column yet: look again tomorrow, not on every edit
        idempotency.claim(once, ttl=RETRY_S)
        return False
    return True

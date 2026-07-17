"""Check that a Meta ad account is reachable for the campaign report (T7).

Read-only: resolves the system-user token, reads the account's name and currency,
and pulls a one-day insights slice to prove the token has ``ads_read`` on that
account. Run it before trusting a report — a missing partner grant or an
unassigned asset surfaces here as a clear 401/permission error rather than an
empty PDF.

Usage:
    # An account id from ClickUp's חשבון מודעות Meta field, act_ or bare digits:
    python -m src.tools.check_meta --account act_1234567890

    # Or fall back to META_AD_ACCOUNT_ID in .env (the only thing that var is for):
    python -m src.tools.check_meta

Exit code is 0 when the account reads cleanly, 1 otherwise — so it can gate a
deploy or a first live run.
"""

from __future__ import annotations

import argparse
import sys

from ..lib import campaign_metrics, config
from ..lib.clients.meta_ads import MetaAdsClient, MetaError

NAME = "check_meta"


def check(account_id: str) -> bool:
    client = MetaAdsClient()
    try:
        account = client.account(account_id)
    except MetaError as exc:
        print(f"[!!] could not read the account: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 - surface the Graph error, not a stack trace
        print(f"[!!] {exc}")
        print("     Check the token has ads_read and this account is assigned to "
              "the system user (Business Settings → System Users → Assign Assets).")
        return False

    symbol = campaign_metrics.currency_symbol(account.get("currency") or "")
    print(f"[ok] account: {account.get('name') or '(no name)'}  "
          f"({account['id']}, {account.get('currency')} {symbol})")

    # A one-day slice proves ads_read without pulling a whole month.
    today = config.get("META_CHECK_DAY")  # optional override for a known-active day
    since = until = today or "2026-01-01"
    try:
        rows = client.insights(account_id, since=since, until=until)
    except Exception as exc:  # noqa: BLE001
        print(f"[!!] insights call failed: {exc}")
        return False
    print(f"[ok] insights readable — {len(rows)} campaign row(s) for {since}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__ or NAME)
    parser.add_argument(
        "--account",
        help="Ad account id (act_… or bare digits). "
             "Defaults to META_AD_ACCOUNT_ID in .env.",
    )
    args = parser.parse_args()
    config.load_dotenv()

    account_id = args.account or config.get("META_AD_ACCOUNT_ID")
    if not account_id:
        parser.error("no account: pass --account or set META_AD_ACCOUNT_ID in .env")

    sys.exit(0 if check(account_id) else 1)


if __name__ == "__main__":
    main()

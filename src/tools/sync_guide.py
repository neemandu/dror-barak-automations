"""Publish Dror's operator guide (docs/OPERATIONS.md) as a ClickUp Doc.

``docs/OPERATIONS.md`` stays the single source: it lives with the code, and every
change to an automation updates it (CLAUDE.md). Dror reads it where he works, as
the "📘 מדריך המערכת" Doc in ClickUp, one page per ``##`` section. This rewrites
those pages to match the file; ``deploy_stack`` runs it after every successful
deploy, so the guide in ClickUp is never behind what is running.

The Doc is generated: an edit made in ClickUp is overwritten by the next sync,
and the first page says so. Pages are matched by name; a section whose heading
changed reuses a page no longer matched, so the Doc does not grow a trail of
stale pages.

Usage:
    python -m src.tools.sync_guide            # CLICKUP_GUIDE_DOC_ID from .env
    python -m src.tools.sync_guide --create   # make the Doc in the space of the
                                              # משימות list, print its id for .env
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from ..lib import config
from ..lib.http import request

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "OPERATIONS.md"
DOC_NAME = "📘 מדריך המערכת"
FIRST_PAGE = "על המדריך"
NOTICE = ("> המדריך מתעדכן אוטומטית מהמערכת בכל עדכון שלה. שינוי שנכתב כאן יימחק בעדכון "
          "הבא, אז בקשות לשינוי כותבים כמשימה.")


def sections(markdown: str) -> list[tuple[str, str]]:
    """``[(page name, page markdown)]``: the text before the first ``##`` is the
    first page, then one page per ``##`` section (its ``###`` stay inside)."""
    text = re.sub(r"</?div[^>]*>", "", markdown)          # the file's RTL wrapper
    text = re.sub(r"(?m)^# .*\n", "", text, count=1)      # the title is the Doc's name
    parts = re.split(r"(?m)^## (.+)$", text)
    pages = [(FIRST_PAGE, f"{NOTICE}\n\n{parts[0].strip()}")]
    for i in range(1, len(parts), 2):
        pages.append((parts[i].strip(), parts[i + 1].strip()))
    return pages


def _v3() -> str:
    return f"https://api.clickup.com/api/v3/workspaces/{config.require('CLICKUP_TEAM_ID')}"


def _headers() -> dict[str, str]:
    return {"Authorization": config.require("CLICKUP_API_TOKEN")}


def _flatten(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for p in pages:
        out.append(p)
        out += _flatten(p.get("pages") or [])
    return out


def create_doc() -> str:
    tasks_list = request("GET", f"https://api.clickup.com/api/v2/list/{config.require('CLICKUP_TASKS_LIST_ID')}",
                         headers=_headers()).json()
    space = str((tasks_list.get("space") or {}).get("id"))
    doc = request("POST", f"{_v3()}/docs", headers=_headers(),
                  json={"name": DOC_NAME, "parent": {"id": space, "type": 4}, "create_page": False}).json()
    return str(doc["id"])


def sync(doc_id: str, *, dry_run: bool = False) -> dict[str, int]:
    wanted = sections(GUIDE.read_text(encoding="utf-8"))
    base = f"{_v3()}/docs/{doc_id}/pages"
    existing = _flatten(request("GET", base, headers=_headers(),
                                params={"max_page_depth": "-1"}).json() if not dry_run else [])
    by_name = {str(p.get("name")): p for p in existing}
    spare = [p for p in existing if str(p.get("name")) not in {n for n, _ in wanted}]
    made = updated = 0
    for name, content in wanted:
        page = by_name.get(name) or (spare.pop(0) if spare else None)
        if dry_run:
            continue
        if page:
            request("PUT", f"{base}/{page['id']}", headers=_headers(),
                    json={"name": name, "content": content, "content_format": "text/md",
                          "content_edit_mode": "replace"})
            updated += 1
        else:
            request("POST", base, headers=_headers(),
                    json={"name": name, "content": content, "content_format": "text/md"})
            made += 1
    return {"pages": len(wanted), "updated": updated, "created": made, "unused": len(spare)}


def sync_if_configured() -> None:
    """For deploy_stack: best-effort, never fails a deploy that already succeeded."""
    doc_id = config.get("CLICKUP_GUIDE_DOC_ID")
    if not doc_id:
        return
    try:
        result = sync(str(doc_id))
        print(f"guide synced to ClickUp: {result['pages']} pages "
              f"({result['updated']} updated, {result['created']} new)")
    except Exception as exc:  # noqa: BLE001
        print(f"guide NOT synced to ClickUp: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--create", action="store_true", help="Create the Doc and print its id")
    args = parser.parse_args()
    config.load_dotenv()
    if args.create:
        print(f"CLICKUP_GUIDE_DOC_ID={create_doc()}")
        return
    doc_id = config.get("CLICKUP_GUIDE_DOC_ID")
    if not doc_id:
        print("CLICKUP_GUIDE_DOC_ID is not set; run with --create first.")
        sys.exit(1)
    print(sync(str(doc_id)))


if __name__ == "__main__":
    main()

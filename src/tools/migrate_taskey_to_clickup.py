"""Migrate clients from a Taskey export into ClickUp (the new CRM).

Reads a CSV export of Taskey clients and creates one ClickUp task per client in
the target list, mapping Taskey columns onto ClickUp custom fields where they
exist and preserving *everything* in the task description as a fallback so no data
is lost even before the custom fields are set up.

Column names in the Taskey export are not known ahead of time, so matching is
done by a set of aliases (Hebrew + English) per canonical field. Override any
mapping with a ``--mapping`` JSON file: ``{"monthly_price": "העמודה שלי", ...}``.

Usage:
    # See exactly what would be created, no writes, no credentials needed:
    python -m src.tools.migrate_taskey_to_clickup --input examples/taskey_sample.csv --dry-run

    # Real migration (needs CLICKUP_API_TOKEN and a list id):
    python -m src.tools.migrate_taskey_to_clickup \\
        --input taskey_export.csv --list-id 901234567

Safety: run with ``--dry-run`` first and read the printed mapping. Use ``--limit``
to migrate just the first N rows as a live smoke test before the full run.
"""

from __future__ import annotations

import argparse
import csv
import json
from typing import Any, Optional

from ..lib import config
from ..lib.clients.clickup import ClickUpClient
from ..automations.base import Automation

NAME = "migrate_taskey_to_clickup"

# Canonical client fields -> possible Taskey/ClickUp column names (normalized).
ALIASES: dict[str, list[str]] = {
    "name": ["name", "client", "לקוח", "שם", "שם לקוח", "שם הלקוח"],
    "phone": ["phone", "mobile", "טלפון", "נייד", "מספר טלפון"],
    "email": ["email", "mail", "מייל", "אימייל", 'דוא"ל', "דואל"],
    "status": ["status", "סטטוס", "סטטוס ראשי"],
    "sub_status": ["sub status", "substatus", "סטטוס משני"],
    "monthly_price": ["monthly price", "price", "מחיר חודשי", "מחיר", "ריטיינר"],
    "service_type": ["service type", "service", "סוג שירות"],
    "drive_folder": [
        "drive", "drive folder", "נתיב תיקיית drive", "תיקיית drive", "דרייב"
    ],
    "signed_contract": ["signed contract", "contract", "חוזה חתום", "חוזה"],
    "recordings_path": ["recordings", "נתיב הקלטות", "הקלטות"],
    "morning_status": ["morning", "morning status", "סטטוס morning"],
}

# Fields that should be sent to ClickUp as numbers rather than text.
NUMERIC_FIELDS = {"monthly_price"}


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().casefold().split())


def _canonical_for(header: str) -> Optional[str]:
    """Return the canonical field a raw column/field name maps to, if any."""
    norm = _normalize(header)
    for canonical, names in ALIASES.items():
        if norm in names or norm == canonical:
            return canonical
    return None


def _map_headers(headers: list[str], overrides: dict[str, str]) -> dict[str, str]:
    """Return ``{canonical: actual_header}`` for the CSV columns.

    ``overrides`` (from --mapping) wins over the alias auto-detection.
    """
    mapping: dict[str, str] = {}
    for header in headers:
        canonical = _canonical_for(header)
        if canonical and canonical not in mapping:
            mapping[canonical] = header
    mapping.update({k: v for k, v in overrides.items() if v in headers})
    return mapping


def _to_canonical(row: dict[str, str], header_map: dict[str, str]) -> dict[str, str]:
    return {c: (row.get(h) or "").strip() for c, h in header_map.items()}


def _resolve_field_ids(clickup: ClickUpClient, list_id: str) -> dict[str, str]:
    """Return ``{canonical: clickup_field_id}`` for fields that exist in the list."""
    resolved: dict[str, str] = {}
    for field in clickup.get_list_fields(list_id):
        canonical = _canonical_for(field.get("name", ""))
        if canonical:
            resolved[canonical] = field["id"]
    return resolved


def _build_description(client: dict[str, str]) -> str:
    lines = ["Migrated from Taskey.", "", "## Fields"]
    for canonical, value in client.items():
        if value:
            lines.append(f"- **{canonical}**: {value}")
    return "\n".join(lines)


def _coerce(canonical: str, value: str) -> Any:
    if canonical in NUMERIC_FIELDS:
        digits = "".join(ch for ch in value if ch.isdigit() or ch == ".")
        try:
            return float(digits) if digits else None
        except ValueError:
            return None
    return value


def run(
    input_path: str,
    *,
    list_id: str,
    dry_run: bool = False,
    overrides: Optional[dict[str, str]] = None,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    clickup = ClickUpClient(dry_run=dry_run)
    overrides = overrides or {}

    with open(input_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        header_map = _map_headers(headers, overrides)
        rows = list(reader)

    field_ids = _resolve_field_ids(clickup, list_id)
    auto.log_action(
        "mapping_resolved",
        detail=f"{len(header_map)} columns mapped; "
        f"{len(field_ids)} ClickUp custom fields matched",
        columns=header_map,
        clickup_fields=list(field_ids),
    )

    created: list[dict[str, Any]] = []
    for i, raw in enumerate(rows):
        if limit is not None and i >= limit:
            auto.log_action("limit_reached", "skipped", detail=f"stopped at {limit}")
            break
        client = _to_canonical(raw, header_map)
        name = client.get("name") or f"Taskey client {i + 1}"

        custom_fields = [
            {"id": field_ids[c], "value": _coerce(c, client[c])}
            for c in field_ids
            if client.get(c)
        ]
        try:
            task = clickup.create_task(
                list_id,
                name,
                description=_build_description(client),
                custom_fields=custom_fields or None,
            )
            created.append(task)
            auto.log_action(
                "task_created",
                client_id=name,
                detail=task.get("url"),
                custom_fields=len(custom_fields),
            )
        except Exception as exc:  # keep migrating the rest
            auto.log_action("task_error", "error", client_id=name, detail=str(exc))

    auto.log_action("migration_done", detail=f"{len(created)}/{len(rows)} tasks created")
    return {"total": len(rows), "created": len(created), "mapping": header_map}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Taskey clients into ClickUp")
    parser.add_argument("--input", required=True, help="Path to the Taskey CSV export")
    parser.add_argument("--list-id", help="ClickUp target list id (or CLICKUP_CRM_LIST_ID)")
    parser.add_argument("--mapping", help="JSON file: {canonical_field: csv_header}")
    parser.add_argument("--limit", type=int, help="Migrate only the first N rows")
    parser.add_argument("--dry-run", action="store_true", help="No writes; print the plan")
    config.load_dotenv()
    args = parser.parse_args()

    list_id = args.list_id or config.get("CLICKUP_CRM_LIST_ID")
    if not list_id and not args.dry_run:
        parser.error("--list-id (or CLICKUP_CRM_LIST_ID) is required for a live run")

    overrides = {}
    if args.mapping:
        with open(args.mapping, encoding="utf-8") as fh:
            overrides = json.load(fh)

    result = run(
        args.input,
        list_id=list_id or "dry-run-list",
        dry_run=args.dry_run,
        overrides=overrides,
        limit=args.limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Templates Dror picks per client, copied into that client's Drive folder.

The library is one Drive folder (``DRIVE_TEMPLATES_FOLDER_ID``, "טמפלטים ללקוחות"):
every file in it is a template, and its name (without the extension) is the
template's name. Dror picks, per client, in the ``תבניות`` Labels field on the
client's ClickUp task; one label per template, named like the file.

Whenever the field changes (the לקוחות webhook's ``taskUpdated``), and once more
at onboarding, this copies each picked template that the client's folder does
not have yet, named ``<template> - <client>``. Office files are converted on the
way (an .xlsx becomes a Google Sheet, a .docx a Google Doc), so the client opens
them in the browser. Un-picking a label deletes nothing: the copy may already
hold work. The client's name is not filled into the copy (Dror's call).

A lead has no folder yet: their picks wait for onboarding, which makes the folder
and runs this, so two updates can never race to create two folders. A picked
label with no file of that name is said on the task (once a day), not skipped
silently.

The client's folder is shared with them (:func:`src.lib.client_folder.share_with_client`)
at onboarding; a client onboarded before that existed is shared the first time a
template is copied for them.

Manual/dry-run:
    python -m src.automations.client_templates --client-id 42 --dry-run
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..lib import client_folder, config, crm_fields, idempotency
from ..lib.clients.crm import STATUS_ACTIVE, CrmClient
from ..lib.clients.google import GoogleClient
from .base import Automation, build_arg_parser, run_cli

NAME = "client_templates"
FIELD = "תבניות"

# Uploaded Office files become their Google equivalent in the copy.
_CONVERT = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "application/vnd.google-apps.document",
    "application/msword": "application/vnd.google-apps.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "application/vnd.google-apps.spreadsheet",
    "application/vnd.ms-excel": "application/vnd.google-apps.spreadsheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "application/vnd.google-apps.presentation",
}

# Two updates a second apart run two jobs; each template is copied by one of them.
_COPY_WINDOW = 10 * 60
_MISSING_WINDOW = 24 * 60 * 60


def template_name(file_name: str) -> str:
    """A file's template name: its name without an Office extension."""
    stem, ext = os.path.splitext(file_name)
    return stem if ext.lower() in (".docx", ".doc", ".xlsx", ".xls", ".pptx", ".pdf") else file_name


def copy_name(template: str, client_name: str) -> str:
    return f"{template} - {client_name}"


def picked(task: dict[str, Any]) -> list[str]:
    """The template names picked in the task's ``תבניות`` Labels field."""
    wanted = crm_fields.normalize(FIELD)
    for field in task.get("custom_fields") or []:
        if crm_fields.normalize(str(field.get("name") or "")) != wanted:
            continue
        options = (field.get("type_config") or {}).get("options") or []
        by_id = {str(o.get("id")): str(o.get("label") or o.get("name") or "") for o in options}
        out = []
        for value in field.get("value") or []:
            key = str(value.get("id") if isinstance(value, dict) else value)
            label = by_id.get(key) or (str(value.get("label") or "") if isinstance(value, dict) else "")
            if label:
                out.append(label)
        return out
    return []


def library(google: GoogleClient) -> dict[str, dict[str, Any]]:
    """``{template name: file}`` for the templates folder."""
    folder = config.require("DRIVE_TEMPLATES_FOLDER_ID")
    return {template_name(str(f.get("name"))): f for f in google.list_folder(folder)
            if f.get("mimeType") != client_folder.FOLDER_MIME}


def _onboarded(client: dict[str, Any]) -> bool:
    return str(client.get("status") or "") == STATUS_ACTIVE


def picked_for(client_id: str, *, dry_run: bool = False) -> list[str]:
    from ..lib.clients.clickup import ClickUpClient

    return picked(ClickUpClient(dry_run=dry_run).get_task(client_id))


def run(client_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    """The ``תבניות`` field changed (or may have): copy what is picked and missing."""
    auto = Automation(NAME, dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    names = picked_for(client_id, dry_run=dry_run)
    if not names:
        return {"picked": []}
    client = {**crm.get_client(client_id), "id": client_id}
    recorded = str(client.get("drive_folder_url") or client.get("drive_folder_path") or "")
    folder_id = client_folder.folder_id_from(recorded)
    if not folder_id:
        return {"picked": names, "waiting": "no folder yet: copied at onboarding"}
    return copy_picked(auto, crm, GoogleClient(dry_run=dry_run), client, folder_id, names,
                       dry_run=dry_run)


def copy_picked(auto: Automation, crm: CrmClient, google: GoogleClient, client: dict[str, Any],
                folder_id: str, names: list[str], *, dry_run: bool = False) -> dict[str, Any]:
    """Copy each of ``names`` the folder does not have yet; say what is missing."""
    client_id = str(client["id"])
    client_name = str(client.get("name") or client_id)
    recorded = f"https://drive.google.com/drive/folders/{folder_id}"
    if not names:
        return {"picked": [], "copied": [], "missing": []}
    lib = library(google)
    present = {str(f.get("name")) for f in google.list_folder(folder_id)}
    copied, missing = [], []
    for name in names:
        source = lib.get(name)
        if source is None:
            missing.append(name)
            continue
        title = copy_name(name, client_name)
        if title in present:
            continue
        if not idempotency.claim(f"tmplcopy:{client_id}:{name}", ttl=_COPY_WINDOW):
            continue  # another run is copying it right now
        try:
            made = google.copy_file(str(source["id"]), title, folder_id,
                                    mime_type=_CONVERT.get(str(source.get("mimeType"))))
        except Exception:
            idempotency.release(f"tmplcopy:{client_id}:{name}")
            raise
        url = str(made.get("webViewLink") or "")
        copied.append((title, url))
        auto.log_action("template_copied", client_id=client_id, detail=title, url=url or recorded)

    if copied:
        crm.append_automation_log(client_id, "📄 הועתקו לתיקיית הלקוח:\n" + "\n".join(
            f"{t}: {u}" if u else t for t, u in copied))
        if _onboarded(client):
            # Onboarded before sharing existed: this is when they get access.
            share_folder(auto, crm, client, folder_id, dry_run=dry_run)

    fresh_missing = [n for n in missing
                     if idempotency.claim(f"tmplmissing:{client_id}:{n}", ttl=_MISSING_WINDOW)]
    if fresh_missing:
        crm.append_automation_log(
            client_id, "⚠️ לא מצאתי בתיקיית התבניות קובץ בשם: " + ", ".join(fresh_missing)
            + "\nשם התווית בשדה 'תבניות' צריך להיות כמו שם הקובץ בתיקייה.")
        auto.log_action("template_missing", "error", client_id=client_id,
                        detail=", ".join(fresh_missing))
    return {"picked": names, "copied": [t for t, _ in copied], "missing": missing}


def share_folder(auto: Automation, crm: CrmClient, client: dict[str, Any], folder_id: str,
                 *, dry_run: bool = False) -> Optional[str]:
    """Give the client edit access to their folder. Best-effort, logged either way."""
    client_id = str(client["id"])
    email = str(client.get("email") or "").strip()
    if not email:
        auto.log_action("folder_not_shared", "skipped", client_id=client_id,
                        detail="אין ללקוח מייל ב-ClickUp")
        return None
    try:
        shared = client_folder.share_with_client(folder_id, email, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - the folder and templates stand without it
        auto.log_action("folder_share_failed", "error", client_id=client_id, detail=str(exc))
        return None
    if shared:
        auto.log_action("folder_shared", client_id=client_id, detail=email,
                        url=f"https://drive.google.com/drive/folders/{folder_id}")
        crm.append_automation_log(client_id, f"🔓 תיקיית הלקוח שותפה עם {email} (עריכה)")
    return email


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--client-id", required=True, help="ClickUp task id of the client")
    run_cli(parser, lambda a: run(a.client_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()

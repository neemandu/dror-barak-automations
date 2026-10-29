"""What the contract flow keeps between requests.

Two kinds of record, in the questionnaire table (the app's small document store:
``Retain`` on deploy, point-in-time backups, and every function that needs these
already reads and writes it):

* ``contract:provider_signature``: Dror's own signature, drawn once in the
  dashboard and placed in the provider's box of every contract.
* ``contract:draft:<client_id>``: Dror's own version of the contract for this
  client, edited on the dashboard (:mod:`src.lib.contract_edit`): the whole
  template with his changes, so a later change to the standard cannot alter it.
* ``contract:sent:<client_id>``: that version as it was when the contract was
  sent. The signing page renders from it, so what the client reads and signs is
  what Dror approved even if he edits again; without one the standard is used.
* ``contract:signed:<client_id>``: a client's signature the moment they sign:
  the details they typed, the document exactly as signed, and the audit record
  (time, IP, hash of that document). The signing page stores this and answers at
  once; filing the PDF (render, Drive, ClickUp, emails) happens in the background
  from this record, so nothing the client did can be lost to a slow Drive call,
  and a failed filing can simply be run again.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Optional

from . import questionnaire_store

PROVIDER_KEY = "contract:provider_signature"
SIGNED_PREFIX = "contract:signed:"
DRAFT_PREFIX = "contract:draft:"
SENT_PREFIX = "contract:sent:"

# The status of a signing record.
RECEIVED = "received"  # signed, not yet filed
FILED = "filed"        # PDF stored, task updated, emails sent
SUPERSEDED = "superseded"  # a newer quote was sent after it


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ------------------------------------------------------- Dror's signature


def provider_signature() -> Optional[dict[str, Any]]:
    """``{"png": <base64>, "updated_at": iso}`` or None when Dror has not set one."""
    rec = questionnaire_store._store().get(PROVIDER_KEY)
    return rec if rec and rec.get("png") else None


def provider_signature_img() -> str:
    """Dror's signature as an ``<img>`` for the provider's box, or "" when unset."""
    rec = provider_signature()
    if not rec:
        return ""
    return ('<img class="sig-img" alt="חתימת נותן השירות" '
            f'src="data:image/png;base64,{rec["png"]}">')


def set_provider_signature(png: bytes) -> dict[str, Any]:
    item = {"pk": PROVIDER_KEY, "png": base64.b64encode(png).decode("ascii"), "updated_at": _now()}
    questionnaire_store._store().put(item)
    return item


def clear_provider_signature() -> None:
    questionnaire_store._store().delete(PROVIDER_KEY)


# ------------------------------------------------------- client signatures


def save_signed(client_id: str, *, client_name: str, fields: dict[str, str],
                body: str, audit: dict[str, Any]) -> dict[str, Any]:
    """Keep a signature as the exact document that was hashed (``body``, both
    signatures inside it), so a filing run days later still prints that document
    even if the template changed in between."""
    item = {
        "pk": SIGNED_PREFIX + client_id, "client_id": client_id, "client_name": client_name,
        "fields": dict(fields), "body": body, "audit": dict(audit),
        "status": RECEIVED, "received_at": _now(), "attempts": 0,
    }
    questionnaire_store._store().put(item)
    return item


def get_signed(client_id: str) -> Optional[dict[str, Any]]:
    return questionnaire_store._store().get(SIGNED_PREFIX + client_id)


def current_signature(client_id: str) -> Optional[dict[str, Any]]:
    """The client's signature on the contract currently offered, if they gave it.
    One a newer quote superseded does not count."""
    rec = get_signed(client_id)
    return rec if rec and rec.get("status") in (RECEIVED, FILED) else None


def update_signed(client_id: str, **changes: Any) -> dict[str, Any]:
    rec = get_signed(client_id)
    if not rec:
        raise KeyError(client_id)
    rec.update(changes)
    questionnaire_store._store().put(rec)
    return rec


def list_signed() -> list[dict[str, Any]]:
    return questionnaire_store._store().scan(SIGNED_PREFIX)


def supersede(client_id: str) -> None:
    """A new quote replaces the contract a client signed before: that signature
    stays on record (its PDF is in Drive and the run-log), but no longer answers
    for the new link."""
    rec = get_signed(client_id)
    if rec and rec.get("status") != SUPERSEDED:
        rec["status"] = SUPERSEDED
        questionnaire_store._store().put(rec)


# ------------------------------------------------------- a client's own version


def draft(client_id: str) -> Optional[dict[str, Any]]:
    """``{"template", "base", "summary", "changes", "updated_at"}``: Dror's version
    for this client, or None when the client gets the standard contract."""
    rec = questionnaire_store._store().get(DRAFT_PREFIX + client_id)
    return rec if rec and rec.get("template") else None


def save_draft(client_id: str, template: str, *, base: str, summary: str, changes: int) -> dict[str, Any]:
    item = {"pk": DRAFT_PREFIX + client_id, "client_id": client_id, "template": template,
            "base": base, "summary": summary, "changes": int(changes), "updated_at": _now()}
    questionnaire_store._store().put(item)
    return item


def clear_draft(client_id: str) -> None:
    questionnaire_store._store().delete(DRAFT_PREFIX + client_id)


def sent(client_id: str) -> Optional[dict[str, Any]]:
    """The client's own version as it was sent, or None when the standard was."""
    rec = questionnaire_store._store().get(SENT_PREFIX + client_id)
    return rec if rec and rec.get("template") else None


def sent_template(client_id: str) -> Optional[str]:
    rec = sent(client_id)
    return str(rec["template"]) if rec else None


def freeze_sent(client_id: str) -> Optional[dict[str, Any]]:
    """At sending: the version that goes out is the draft as it stands now, or the
    standard when there is none (and an earlier frozen version no longer applies)."""
    rec = draft(client_id)
    if not rec:
        questionnaire_store._store().delete(SENT_PREFIX + client_id)
        return None
    item = {"pk": SENT_PREFIX + client_id, "client_id": client_id, "template": rec["template"],
            "summary": rec.get("summary", ""), "changes": rec.get("changes", 0), "sent_at": _now()}
    questionnaire_store._store().put(item)
    return item

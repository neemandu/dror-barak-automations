"""Where questionnaires and their answers live.

DynamoDB (``QUESTIONNAIRE_TABLE``) on AWS, a JSON file locally and in tests
(``QUESTIONNAIRE_PATH``, default ``logs/questionnaires.json``). One table, one
string key, three kinds of item:

* ``def:<id>`` — a questionnaire definition (see :mod:`src.lib.questionnaire`).
* ``settings`` — which questionnaire onboarding sends.
* ``resp:<client id>:<questionnaire id>`` — one client's copy of one
  questionnaire: when it was sent, and once answered, the answers plus a snapshot
  of the questions they answered.

Saving a definition is optimistic: the editor sends the version it loaded, and a
save against a newer version is refused (:class:`Conflict`) rather than silently
overwriting someone else's edit.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from . import config, questionnaire

_LOCK = threading.Lock()


class Conflict(RuntimeError):
    """The definition changed since the editor loaded it."""


class Invalid(ValueError):
    """The definition would make an unusable form. ``errors`` is Hebrew."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plain(value: Any) -> Any:
    """DynamoDB hands numbers back as Decimal; the rest of the code wants JSON."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


# ------------------------------------------------------------------ backends


class _FileStore:
    def __init__(self) -> None:
        self.path = Path(config.get("QUESTIONNAIRE_PATH") or "logs/questionnaires.json")

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, pk: str) -> Optional[dict[str, Any]]:
        return self._read().get(pk)

    def put(self, item: dict[str, Any], *, expect_version: Optional[int] = None) -> None:
        with _LOCK:
            data = self._read()
            if expect_version is not None:
                current = int((data.get(item["pk"]) or {}).get("version") or 0)
                if current != expect_version:
                    raise Conflict(f"version is {current}, not {expect_version}")
            data[item["pk"]] = item
            self._write(data)

    def delete(self, pk: str) -> None:
        with _LOCK:
            data = self._read()
            data.pop(pk, None)
            self._write(data)

    def scan(self, prefix: str) -> list[dict[str, Any]]:
        return [v for k, v in self._read().items() if k.startswith(prefix)]


class _DynamoStore:
    def __init__(self, table: str) -> None:
        import boto3

        self.table = boto3.resource(
            "dynamodb", region_name=config.get("AWS_REGION", "eu-central-1")).Table(table)

    def get(self, pk: str) -> Optional[dict[str, Any]]:
        item = self.table.get_item(Key={"pk": pk}).get("Item")
        return _plain(item) if item else None

    def put(self, item: dict[str, Any], *, expect_version: Optional[int] = None) -> None:
        kwargs: dict[str, Any] = {"Item": item}
        if expect_version is not None:
            if expect_version == 0:
                kwargs["ConditionExpression"] = "attribute_not_exists(pk) OR version = :v"
            else:
                kwargs["ConditionExpression"] = "version = :v"
            kwargs["ExpressionAttributeValues"] = {":v": expect_version}
        try:
            self.table.put_item(**kwargs)
        except self.table.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise Conflict("the definition changed since it was loaded") from exc

    def delete(self, pk: str) -> None:
        self.table.delete_item(Key={"pk": pk})

    def scan(self, prefix: str) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Attr

        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"FilterExpression": Attr("pk").begins_with(prefix)}
        while True:
            page = self.table.scan(**kwargs)
            items += page.get("Items", [])
            if "LastEvaluatedKey" not in page:
                return [_plain(i) for i in items]
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _store() -> Any:
    table = config.get("QUESTIONNAIRE_TABLE")
    return _DynamoStore(table) if table else _FileStore()


# --------------------------------------------------------------- definitions


def _seed_if_empty(store: Any) -> None:
    """First use: the questionnaire clients have been getting becomes the default."""
    if store.get("settings") or store.scan("def:"):
        return
    defn = questionnaire.default_definition()
    defn["updated_at"] = _now()
    store.put({"pk": f"def:{defn['id']}", "version": defn["version"], "definition": defn})
    store.put({"pk": "settings", "default_id": defn["id"]})


def list_definitions() -> list[dict[str, Any]]:
    store = _store()
    _seed_if_empty(store)
    defs = [i["definition"] for i in store.scan("def:")]
    return sorted(defs, key=lambda d: str(d.get("title") or ""))


def get_definition(qid: str) -> Optional[dict[str, Any]]:
    store = _store()
    _seed_if_empty(store)
    item = store.get(f"def:{qid}")
    return item["definition"] if item else None


def default_id() -> str:
    store = _store()
    _seed_if_empty(store)
    settings = store.get("settings") or {}
    qid = str(settings.get("default_id") or "")
    if qid and store.get(f"def:{qid}"):
        return qid
    defs = store.scan("def:")
    return str(defs[0]["definition"]["id"]) if defs else questionnaire.DEFAULT_DEFINITION["id"]


def set_default(qid: str) -> None:
    store = _store()
    if not store.get(f"def:{qid}"):
        raise KeyError(qid)
    store.put({"pk": "settings", "default_id": qid})


def save_definition(defn: dict[str, Any], *, expected_version: int) -> dict[str, Any]:
    """Validate and store; returns the stored definition with its new version."""
    clean = questionnaire.normalize(defn)
    errors = questionnaire.validate(clean)
    if errors:
        raise Invalid(errors)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,40}", clean["id"]):
        raise Invalid(["מזהה שאלון לא תקין."])
    clean["version"] = expected_version + 1
    clean["updated_at"] = _now()
    _store().put({"pk": f"def:{clean['id']}", "version": clean["version"], "definition": clean},
                 expect_version=expected_version)
    return clean


def create_definition(title: str, *, copy_from: Optional[str] = None) -> dict[str, Any]:
    """A new questionnaire — blank, or a copy of an existing one with fresh keys."""
    qid = questionnaire.new_key("qn").replace("_", "-")
    if copy_from:
        source = get_definition(copy_from)
        if not source:
            raise KeyError(copy_from)
        defn = json.loads(json.dumps(source))
        defn.update(id=qid, title=title or f"{source.get('title')} (עותק)", version=0)
    else:
        defn = questionnaire.blank_definition(qid, title)
    return save_definition(defn, expected_version=0)


def delete_definition(qid: str) -> None:
    if qid == default_id():
        raise Invalid(["אי אפשר למחוק את השאלון שנשלח ללקוחות. קודם הגדר שאלון אחר כברירת מחדל."])
    _store().delete(f"def:{qid}")


# ----------------------------------------------------------------- responses


def _resp_pk(client_id: str, qid: str) -> str:
    return f"resp:{client_id}:{qid}"


def get_response(client_id: str, qid: str) -> Optional[dict[str, Any]]:
    return _store().get(_resp_pk(client_id, qid))


def definition_for_client(client_id: str) -> dict[str, Any]:
    """The questionnaire this client was sent — or the default, if none recorded.

    A client keeps the questionnaire they were sent even if Dror later makes a
    different one the default; the link in their inbox must not change meaning.
    """
    sent = [r for r in _store().scan(f"resp:{client_id}:")]
    for r in sorted(sent, key=lambda r: str(r.get("sent_at") or ""), reverse=True):
        defn = get_definition(str(r.get("questionnaire_id")))
        if defn:
            return defn
    return get_definition(default_id()) or questionnaire.default_definition()


def record_sent(client_id: str, client_name: str, qid: str, *, via: str = "email") -> dict[str, Any]:
    """Note that a client was sent (or given a link to) a questionnaire."""
    store = _store()
    defn = get_definition(qid) or {}
    item = store.get(_resp_pk(client_id, qid)) or {
        "pk": _resp_pk(client_id, qid), "client_id": client_id, "questionnaire_id": qid,
        "status": "sent", "answers": {}, "snapshot": [], "history": [],
    }
    item.update(client_name=client_name or item.get("client_name") or client_id,
                questionnaire_title=defn.get("title") or item.get("questionnaire_title") or qid,
                sent_at=item.get("sent_at") or _now())
    item.setdefault("history", []).append({"at": _now(), "event": f"sent:{via}"})
    store.put(item)
    return item


def record_answer(client_id: str, client_name: str, defn: dict[str, Any],
                  answers: dict[str, Any], *, doc_url: str = "") -> dict[str, Any]:
    """Store a submission, with the questions as they were when answered."""
    store = _store()
    pk = _resp_pk(client_id, defn["id"])
    item = store.get(pk) or {"pk": pk, "client_id": client_id, "questionnaire_id": defn["id"],
                             "sent_at": "", "history": []}
    was_answered = item.get("status") == "answered"
    item.update(client_name=client_name or item.get("client_name") or client_id,
                questionnaire_title=defn.get("title"), questionnaire_version=defn.get("version"),
                status="answered", answered_at=_now(), answers=answers,
                snapshot=questionnaire.snapshot(defn), doc_url=doc_url or item.get("doc_url", ""))
    item.setdefault("history", []).append({"at": _now(), "event": "updated" if was_answered else "answered"})
    store.put(item)
    return item


def latest_answered(client_id: str) -> Optional[dict[str, Any]]:
    """This client's most recent submitted questionnaire, if any."""
    answered = [r for r in _store().scan(f"resp:{client_id}:") if r.get("status") == "answered"]
    return max(answered, key=lambda r: str(r.get("answered_at") or ""), default=None)


def list_responses(qid: Optional[str] = None) -> list[dict[str, Any]]:
    rows = _store().scan("resp:")
    if qid:
        rows = [r for r in rows if r.get("questionnaire_id") == qid]
    return sorted(rows, key=lambda r: str(r.get("answered_at") or r.get("sent_at") or ""), reverse=True)

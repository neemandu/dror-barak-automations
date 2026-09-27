"""Lead -> Google Contacts: when the phone appears, once per number."""

from __future__ import annotations

import json

import pytest

from src.automations import lead_to_contacts
from src.lib.clients.google import GoogleClient


@pytest.fixture
def saved(monkeypatch):
    out = []
    monkeypatch.setattr(GoogleClient, "create_contact",
                        lambda self, name, phone, email=None: out.append(phone) or {"resourceName": "people/1"})
    return out


def _lead(phone):
    return {"id": "42", "name": "מכללת דוגמה", "phone": phone, "email": ""}


def test_a_phone_typed_after_creation_is_saved_once(saved, read_log):
    lead_to_contacts.run("42", dry_run=True, client=_lead(""), on_update=True)
    assert saved == [] and read_log() == []  # no phone yet: nothing, not even a log line
    lead_to_contacts.run("42", dry_run=True, client=_lead("050-111-2222"), on_update=True)
    lead_to_contacts.run("42", dry_run=True, client=_lead("0501112222"), on_update=True)  # same digits
    assert saved == ["050-111-2222"]
    assert [e["action"] for e in read_log()] == ["contact_saved"]


def test_a_corrected_number_is_saved_too(saved):
    lead_to_contacts.run("42", dry_run=True, client=_lead("0501112222"), on_update=True)
    lead_to_contacts.run("42", dry_run=True, client=_lead("0503334444"), on_update=True)
    assert saved == ["0501112222", "0503334444"]


def test_a_failed_save_is_tried_again_on_the_next_update(monkeypatch):
    calls = []

    def flaky(self, name, phone, email=None):
        calls.append(phone)
        if len(calls) == 1:
            raise RuntimeError("people api down")
        return {"resourceName": "people/1"}

    monkeypatch.setattr(GoogleClient, "create_contact", flaky)
    with pytest.raises(RuntimeError):
        lead_to_contacts.run("42", dry_run=True, client=_lead("0501112222"), on_update=True)
    assert lead_to_contacts.run("42", dry_run=True, client=_lead("0501112222"), on_update=True)["contact"]


def test_a_new_task_without_a_phone_is_still_logged(saved, read_log):
    lead_to_contacts.run("42", dry_run=True, client=_lead(""))
    assert [e["action"] for e in read_log()] == ["no_phone"]


def test_a_client_update_tries_to_save_the_phone(monkeypatch):
    from src import lambda_handler
    from src.lib import tasks

    seen = []
    monkeypatch.setattr(lead_to_contacts, "run", lambda cid, **kw: seen.append((cid, kw)) or {})
    monkeypatch.setattr(lambda_handler, "_sub_status_of", lambda task_id, dry_run: None)
    monkeypatch.setattr(tasks, "dispatch", lambda *a, **k: {"queued": True})
    lambda_handler.route({"event": "taskUpdated", "task_id": "t1", "history_items": []})
    assert seen == [("t1", {"dry_run": False, "on_update": True})]


def test_a_contacts_failure_does_not_stop_onboarding(monkeypatch):
    from src import lambda_handler
    from src.automations import onboarding

    monkeypatch.setattr(lead_to_contacts, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(lambda_handler, "_sub_status_of", lambda task_id, dry_run: "signed")
    monkeypatch.setattr(onboarding, "run", lambda task_id, dry_run=False: {"onboarded": task_id})
    assert lambda_handler.route({"event": "taskUpdated", "task_id": "t2"}) == {"onboarded": "t2"}

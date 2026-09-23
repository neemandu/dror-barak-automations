"""The questionnaire admin: pages render, the API edits safely, access is guarded."""

from __future__ import annotations

import json

import pytest

from src import dashboard, dashboard_lambda as dl, questionnaire_admin as admin
from src.lib import questionnaire_store as store

H = {"x-requested-with": "dashboard"}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "correct-horse")
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    monkeypatch.setenv("IDEMPOTENCY_TABLE", "")
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "i.json"))
    dashboard._attempts.clear()
    yield


def call(method, route, body=None, headers=H, query=None):
    raw = json.dumps(body).encode() if body is not None else b""
    return admin.handle(method, route, query or {}, raw, headers, base="/dev", dry_run=True)


def test_the_pages_render():
    for route in ("/admin/questionnaires", "/admin/questionnaires/strategy",
                  "/admin/questionnaires/strategy/preview", "/admin/responses"):
        resp = call("GET", route)
        assert resp.status == 200, route
    assert "שאלון הכנה לבניית אסטרטגיה" in call("GET", "/admin/questionnaires").body


def test_the_editor_embeds_its_data_safely():
    defn = store.get_definition("strategy")
    defn["title"] = "</script><script>alert(1)</script>"
    store.save_definition(defn, expected_version=defn["version"])
    page = call("GET", "/admin/questionnaires/strategy").body
    assert "</script><script>alert(1)" not in page


def test_create_edit_and_save_through_the_api():
    created = json.loads(call("POST", "/admin/api/questionnaires", {"title": "שאלון היכרות"}).body)
    defn = store.get_definition(created["id"])
    defn["sections"][0]["questions"].append({"label": "מה התקציב?", "kind": "choice",
                                             "options": ["עד 5,000", "מעל 5,000"]})
    resp = call("POST", f"/admin/api/questionnaires/{created['id']}", {"definition": defn, "version": defn["version"]})
    assert resp.status == 200, resp.body
    saved = store.get_definition(created["id"])
    new_q = saved["sections"][0]["questions"][-1]
    assert new_q["label"] == "מה התקציב?" and new_q["key"].startswith("q_"), "a key is generated, not taken from the label"


def test_a_stale_save_gets_409_and_an_invalid_one_422():
    defn = store.get_definition("strategy")
    ok = call("POST", "/admin/api/questionnaires/strategy", {"definition": defn, "version": defn["version"]})
    assert ok.status == 200
    stale = call("POST", "/admin/api/questionnaires/strategy", {"definition": defn, "version": defn["version"]})
    assert stale.status == 409
    defn = store.get_definition("strategy")
    defn["title"] = ""
    bad = call("POST", "/admin/api/questionnaires/strategy", {"definition": defn, "version": defn["version"]})
    assert bad.status == 422 and "כותרת" in bad.body


def test_the_api_refuses_a_request_a_cross_site_form_could_send():
    resp = call("POST", "/admin/api/questionnaires", {"title": "x"}, headers={})
    assert resp.status == 403
    assert len(store.list_definitions()) == 1


def test_a_link_for_a_client_opens_their_questionnaire():
    from src.lib import signing

    resp = json.loads(call("POST", "/admin/api/links", {"client_id": "42", "questionnaire_id": "strategy"}).body)
    token = resp["url"].split("t=")[1]
    assert signing.resolve(token) == "42"
    assert store.get_response("42", "strategy")["history"][-1]["event"] == "sent:link"


def test_export_is_one_row_per_client_with_a_bom():
    defn = store.get_definition("strategy")
    store.record_answer("42", "מכללה", defn, {"business_name": "אלפא", "goals": "צמיחה"})
    resp = call("GET", "/admin/questionnaires/strategy/export.csv")
    assert resp.body.startswith("﻿") and "attachment" in resp.disposition
    lines = resp.body.strip().splitlines()
    assert len(lines) == 2 and "אלפא" in lines[1]


def test_the_admin_on_lambda_requires_a_session():
    event = {"rawPath": "/dev/admin/questionnaires",
             "requestContext": {"stage": "dev", "http": {"method": "GET", "path": "/dev/admin/questionnaires"}}}
    assert dl.lambda_handler(event)["statusCode"] == 303
    api = {"rawPath": "/dev/admin/api/questionnaires", "body": "{}",
           "requestContext": {"stage": "dev", "http": {"method": "POST", "path": "/dev/admin/api/questionnaires"}}}
    assert dl.lambda_handler(api)["statusCode"] == 401
    event["cookies"] = [f"{dl.COOKIE}={dl.issue_session()}"]
    resp = dl.lambda_handler(event)
    assert resp["statusCode"] == 200 and 'href="/dev/admin/responses"' in resp["body"]

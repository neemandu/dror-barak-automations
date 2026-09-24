"""The לקוחות and מסמכים screens: documents from the run-log, the list, the card."""

import pytest

from src import clients_pages as cp
from src import dashboard, dashboard_lambda as dl

ENTRIES = [
    {"ts": "2026-07-14T16:00:00Z", "automation": "strategy_bot", "action": "strategy_ready", "status": "ok",
     "client_id": "מכללת אלפא", "url": "https://docs.google.com/document/d/v1"},
    {"ts": "2026-07-15T16:30:00Z", "automation": "strategy_bot", "action": "strategy_ready", "status": "ok",
     "client_id": "מכללת אלפא", "url": "https://docs.google.com/document/d/v2"},
    {"ts": "2026-07-01T08:00:00Z", "automation": "campaign_summary", "action": "campaign_summary_ready", "status": "ok",
     "client_id": "מכללת אלפא", "url": "https://drive.google.com/file/d/june/view", "detail": "יוני 2026 · 6,335 ₪"},
    {"ts": "2026-07-02T08:00:00Z", "automation": "campaign_summary", "action": "report_failed", "status": "error",
     "client_id": "מכללת אלפא", "detail": "Meta API: token expired"},
    {"ts": "2026-07-03T08:00:00Z", "automation": "strategy_bot", "action": "strategy_ready", "status": "error",
     "client_id": "מכללת בטא", "url": "https://docs.google.com/document/d/broken"},
    {"ts": "2026-09-23T19:05:02Z", "automation": "clickup_to_claude", "action": "drive_doc_created", "status": "ok",
     "client_id": "z90hj30mrh", "url": "https://docs.google.com/document/d/agent", "detail": "3 פוסטים"},
]


def test_documents_are_the_ok_entries_with_a_link_newest_first_latest_marked():
    docs = cp.documents_from(ENTRIES)
    assert [(d["kind"], d["url"].rsplit("/", 1)[-1]) for d in docs] == [
        ("agent", "agent"), ("strategy", "v2"), ("strategy", "v1"), ("campaign", "view")]
    assert [d["latest"] for d in docs] == [True, True, False, True], "a strategy built twice has an older version"


def test_the_documents_page_names_clients_and_marks_versions():
    html = cp.documents_page(ENTRIES, "/dev", dry_run=True)
    assert "העדכני" in html and "גרסה קודמת" in html and "דוח קמפיינים חודשי" in html
    assert 'href="/dev/clients/%D7%9E%D7%9B%D7%9C%D7%9C%D7%AA%20%D7%90%D7%9C%D7%A4%D7%90"' in html
    assert "משימה ב-ClickUp" in html, "the agent's Doc points at its task, not at a client"
    assert "https://docs.google.com/document/d/broken" not in html, "a failed run made no document"


def test_the_clients_page_lists_clickup_with_where_each_stands():
    html = cp.clients_page(ENTRIES, "/dev", dry_run=True)
    for name in ("מכללת דוגמה", "מכללת אלפא", "מכללת בטא"):
        assert name in html
    assert "לקוח פעיל" in html and "ליד" in html


def test_a_client_card_brings_it_together():
    html = cp.client_page(ENTRIES, "/dev", "מכללת אלפא", dry_run=True)
    assert html and "כל הלקוחות" in html
    assert html.count('class="step ') == 5 and "שאלון מולא" in html
    assert "אסטרטגיה שיווקית" in html and "https://drive.google.com/file/d/june/view" in html
    assert "דוח הקמפיינים נכשל" in html, "the activity shows what failed for this client too"
    assert "עדיין לא נשלח שאלון" in html


def test_an_unknown_client_is_not_found(monkeypatch):
    monkeypatch.setattr(cp, "all_clients", lambda dry_run=False: [])
    from src.lib.clients import crm

    monkeypatch.setattr(crm.CrmClient, "get_client", lambda self, cid: (_ for _ in ()).throw(RuntimeError("404")))
    assert cp.client_page(ENTRIES, "", "nobody", dry_run=False) is None


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "correct-horse")
    monkeypatch.setenv("WEBHOOK_DRY_RUN", "1")
    monkeypatch.setattr(dashboard, "_load_leads", lambda: ENTRIES)
    dashboard._attempts.clear()
    login = dl.lambda_handler({"rawPath": "/dev/login", "body": "password=correct-horse",
                               "requestContext": {"stage": "dev", "http": {"method": "POST", "path": "/dev/login", "sourceIp": "1.1.1.1"}}})
    return login["cookies"][0].split(";")[0].split("=", 1)[1]


def _get(path, cookie=None):
    ev = {"rawPath": path, "requestContext": {"stage": "dev", "http": {"method": "GET", "path": path, "sourceIp": "1.1.1.1"}}}
    if cookie:
        ev["cookies"] = [f"{dl.COOKIE}={cookie}"]
    return dl.lambda_handler(ev)


def test_the_routes_need_a_session_and_serve_the_screens(session):
    assert _get("/dev/clients")["statusCode"] == 303
    for path in ("/dev/clients", "/dev/documents", "/dev/clients/%D7%9E%D7%9B%D7%9C%D7%9C%D7%AA%20%D7%90%D7%9C%D7%A4%D7%90"):
        page = _get(path, session)
        assert page["statusCode"] == 200, path
        assert "data-spa" in page["body"]

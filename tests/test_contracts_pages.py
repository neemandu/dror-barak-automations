"""The חוזים screens: where each contract stands, the preview, and Dror's signature."""

import base64
import struct
import zlib

import pytest

from src import clients_pages as cp
from src import contracts_pages as ct
from src import dashboard, dashboard_lambda as dl
from src.lib import contract_store, signing

ALPHA = {"id": "a1", "name": "מכללת אלפא", "email": "alpha@example.com", "phone": "+972501111111",
         "monthly_price": 4900, "sub_status": "quote_sent", "status": "lead"}


@pytest.fixture(autouse=True)
def _provider(monkeypatch, tmp_path):
    for key in ("NAME", "BUSINESS_ID", "ADDRESS", "PHONE", "EMAIL", "BANK", "BANK_BRANCH", "BANK_ACCOUNT"):
        monkeypatch.setenv(f"PROVIDER_{key}", "לדוגמה 12345")
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "i.json"))
    cp._CACHE.update(at=0.0, clients=None)


def png() -> bytes:
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes((i * 7 + j) % 256 for j in range(400 * 3)) for i in range(120))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 400, 120, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _entry(action, ts, **kw):
    return {"ts": ts, "automation": kw.pop("automation", "send_quote"), "action": action, "status": "ok",
            "client_id": "a1", **kw}


def _signed_record(status=contract_store.RECEIVED, received_at=None):
    contract_store.save_signed("a1", client_name="מכללת אלפא", fields={"client_email": "alpha@example.com"},
                               body="<div class=\"contract\">הסכם</div>",
                               audit=signing.audit_record("a1", "x", ip="1.2.3.4", signed_at="2026-09-20T08:00:00Z"))
    changes = {"status": status}
    if received_at:
        changes["received_at"] = received_at
    return contract_store.update_signed("a1", **changes)


def test_waiting_counts_the_reminders_since_the_last_quote():
    entries = [_entry("quote_sent", "2026-09-10T08:00:00Z"),
               _entry("reminder_sent", "2026-09-08T07:00:00Z", automation="sign_reminders"),  # an older quote's
               _entry("reminder_sent", "2026-09-12T07:00:00Z", automation="sign_reminders"),
               _entry("reminder_sent", "2026-09-14T07:00:00Z", automation="sign_reminders")]
    s = ct.state_for(ALPHA, entries, None)
    assert s["state"] == "waiting" and s["reminders"] == 2


def test_a_signature_is_filing_then_signed_or_failed():
    assert ct.state_for(ALPHA, [], _signed_record())["state"] == "filing"
    assert ct.state_for(ALPHA, [], _signed_record(received_at="2026-01-01T00:00:00Z"))["state"] == "failed"
    rec = contract_store.update_signed("a1", status=contract_store.FILED, link="https://drive/x")
    s = ct.state_for(ALPHA, [], rec)
    assert s["state"] == "signed" and s["link"] == "https://drive/x"


def test_a_new_quote_after_an_old_signature_is_waiting_again():
    entries = [_entry("signed", "2026-08-01T08:00:00Z", automation="sign_contract", url="https://drive/old"),
               _entry("quote_sent", "2026-09-10T08:00:00Z")]
    assert ct.state_for(ALPHA, entries, None)["state"] == "waiting"
    assert ct.state_for(ALPHA, entries[:1], None)["state"] == "signed"


def test_signed_in_clickup_without_us_and_never_sent():
    assert ct.state_for({**ALPHA, "sub_status": "in_work"}, [], None)["state"] == "outside"
    assert ct.state_for({**ALPHA, "sub_status": "initial_meeting"}, [], None)["state"] == "none"


def test_the_preflight_says_what_is_missing_before_sending():
    rows = {title: kind for kind, title, _t in ct.preflight(ALPHA)}
    assert rows["מחיר אחד: 4,900 ₪"] == "warn", "one ClickUp price becomes the strategy line only"
    assert rows["אין חתימה שלך"] == "warn"
    assert rows["מייל"] == "ok"
    split = {title: kind for kind, title, _t in ct.preflight({**ALPHA, "price_strategy": 3000, "price_campaigns": 1500})}
    assert split["מחיר"] == "ok"
    none = {title: kind for kind, title, _t in ct.preflight({**ALPHA, "monthly_price": None, "email": ""})}
    assert none["אין מחיר"] == "err" and none["אין מייל ב-ClickUp"] == "warn"


def test_the_preview_is_the_contract_as_it_will_go_out(monkeypatch):
    monkeypatch.setattr(cp, "all_clients", lambda dry_run=False: [ALPHA])
    contract_store.set_provider_signature(png())
    html = ct.contract_page([], "/dev", "a1", dry_run=True)
    assert "תצוגה מקדימה" in html and "הסכם התקשרות" in html
    assert "ימולא על ידי הלקוח" in html, "the details the client fills are marked, not blank"
    assert 'alt="חתימת נותן השירות"' in html
    assert "לפני השליחה" in html and "4,900" in html


def test_a_signed_contract_shows_the_document_as_signed(monkeypatch):
    monkeypatch.setattr(cp, "all_clients", lambda dry_run=False: [ALPHA])
    _signed_record()
    contract_store.update_signed("a1", status=contract_store.FILED, link="https://drive/x", copy_sent_to="alpha@example.com")
    html = ct.contract_page([], "/dev", "a1", dry_run=True)
    assert "ההסכם כפי שנחתם" in html and "https://drive/x" in html and "alpha@example.com" in html


def test_the_list_and_the_card_show_each_contract(monkeypatch):
    monkeypatch.setattr(cp, "all_clients", lambda dry_run=False: [ALPHA])
    html = ct.contracts_page([_entry("quote_sent", "2026-09-10T08:00:00Z")], "/dev", dry_run=True)
    assert "ממתין לחתימה" in html and "/dev/contracts/a1" in html
    assert "עדיין אין חתימה" in html and "ב-ClickUp יש מחיר חודשי אחד" in html
    card = cp.client_page([_entry("quote_sent", "2026-09-10T08:00:00Z")], "/dev", "a1", dry_run=True)
    assert "<h2 class=\"card-title\">חוזה</h2>" in card and "/dev/contracts/a1" in card


def test_drors_signature_is_saved_from_the_admin_api():
    from src import questionnaire_admin as qa

    def post(body, header="dashboard"):
        import json
        return qa.handle("POST", "/admin/api/signature", {}, json.dumps(body).encode(),
                         {"x-requested-with": header}, dry_run=True)

    assert post({"png": "data:image/png;base64," + base64.b64encode(png()).decode()}, header="").status == 403
    assert post({"png": "data:image/png;base64," + base64.b64encode(png()).decode()}).status == 200
    assert contract_store.provider_signature()["png"]
    assert post({"png": ""}).status == 422
    assert post({"clear": True}).status == 200 and contract_store.provider_signature() is None


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "correct-horse")
    monkeypatch.setenv("WEBHOOK_DRY_RUN", "1")
    monkeypatch.setattr(dashboard, "_load_leads", lambda: [])
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
    assert _get("/dev/contracts")["statusCode"] == 303
    for path in ("/dev/contracts", "/dev/contracts/42"):
        page = _get(path, session)
        assert page["statusCode"] == 200, path
        assert "data-spa" in page["body"] and 'aria-current="page"' in page["body"]

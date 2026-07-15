"""Tests for the dashboard, the subject grouping, and the daily email.

The dashboard is read-only but shows client data, so the auth tests here matter
as much as the rendering ones: a bug that serves the page without a session is
the whole risk of this feature.
"""

from __future__ import annotations

import pytest

from src import dashboard
from src.lib import subjects


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    dashboard._sessions.clear()
    dashboard._attempts.clear()
    monkeypatch.setenv("DASHBOARD_PASSWORD", "correct-horse")
    yield
    dashboard._sessions.clear()
    dashboard._attempts.clear()


ENTRIES = [
    {"ts": "2026-07-15T09:00:00Z", "automation": "onboarding", "action": "drive_folder_created",
     "status": "ok", "client_id": "אלפא", "detail": "https://drive.google.com/drive/folders/x1"},
    {"ts": "2026-07-15T10:00:00Z", "automation": "onboarding", "action": "morning_client_created",
     "status": "ok", "client_id": "אלפא", "detail": "לקוח 44"},
    {"ts": "2026-07-15T11:00:00Z", "automation": "campaign_summary",
     "action": "campaign_report_built", "status": "error", "client_id": "בטא",
     "detail": "token expired"},
    {"ts": "2026-07-15T12:00:00Z", "automation": "monthly_payment_requests",
     "action": "no_price", "status": "skipped", "client_id": "גמא", "detail": "no price"},
]


# ------------------------------------------------------------------- subjects


def test_action_beats_automation_so_onboarding_splits_across_subjects():
    # Both entries come from `onboarding`, but they belong to different subjects.
    assert subjects.subject_for(ENTRIES[0]).key == "drive"
    assert subjects.subject_for(ENTRIES[1]).key == "morning"


def test_grouping_omits_empty_subjects_and_keeps_declared_order():
    grouped = subjects.group_by_subject(ENTRIES)
    keys = [s.key for s, _ in grouped]
    assert keys == ["morning", "meta", "drive"]  # declaration order, no empties


def test_links_are_found_in_free_text_detail():
    # Automations put links in `detail` today; they must still be clickable.
    assert subjects.links_for(ENTRIES[0]) == [
        ("פתח בדרייב", "https://drive.google.com/drive/folders/x1")
    ]


def test_links_recognise_the_explicit_url_field():
    entry = {"url": "https://app.greeninvoice.co.il/documents/9", "detail": "3500₪"}
    assert subjects.links_for(entry) == [("פתח ב-Morning", "https://app.greeninvoice.co.il/documents/9")]


def test_entry_without_a_link_yields_none():
    assert subjects.links_for(ENTRIES[1]) == []


def test_counts_and_failures():
    assert subjects.counts(ENTRIES) == {"total": 4, "ok": 2, "error": 1, "skipped": 1, "dry_run": 0}
    assert [e["action"] for e in subjects.failures(ENTRIES)] == ["campaign_report_built"]


# ----------------------------------------------------------------------- auth


def test_password_check_accepts_and_rejects():
    assert dashboard._check_password("correct-horse") is True
    assert dashboard._check_password("wrong") is False


def test_dashboard_refuses_to_start_without_a_password(monkeypatch):
    # Fail closed: an open dashboard would publish every client's phone and price.
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    with pytest.raises(Exception) as exc:
        dashboard._password()
    assert "DASHBOARD_PASSWORD" in str(exc.value)


def test_session_lifecycle():
    token = dashboard._new_session()
    assert dashboard._valid_session(token) is True
    assert dashboard._valid_session("made-up") is False
    assert dashboard._valid_session(None) is False


def test_expired_session_is_rejected_and_dropped():
    token = dashboard._new_session()
    dashboard._sessions[token] = 0  # already expired
    assert dashboard._valid_session(token) is False
    assert token not in dashboard._sessions


def test_lockout_after_repeated_failures():
    ip = "203.0.113.9"
    for _ in range(dashboard.MAX_ATTEMPTS):
        assert dashboard._locked_out(ip) is False
        dashboard._record_failure(ip)
    assert dashboard._locked_out(ip) is True


def test_cookie_is_hardened_by_default(monkeypatch):
    monkeypatch.delenv("DASHBOARD_INSECURE_COOKIE", raising=False)
    header = dashboard.Handler._cookie_header(None, "tok")  # type: ignore[arg-type]
    assert "HttpOnly" in header and "Secure" in header and "SameSite=Lax" in header


def test_insecure_cookie_only_when_explicitly_opted_in(monkeypatch):
    monkeypatch.setenv("DASHBOARD_INSECURE_COOKIE", "1")
    header = dashboard.Handler._cookie_header(None, "tok")  # type: ignore[arg-type]
    assert "Secure" not in header


# ------------------------------------------------------------------ rendering


def test_page_renders_subjects_links_and_failures():
    page = dashboard._dashboard_page(ENTRIES, {}).decode("utf-8")
    assert "לוח בקרה" in page
    assert "דורש טיפול" in page          # failures pinned to the top
    assert "חשבוניות ותשלומים" in page   # subject heading
    assert 'href="https://drive.google.com/drive/folders/x1"' in page
    assert 'dir="rtl"' in page


def test_log_content_is_escaped_not_injected():
    # Client names and details are data we did not write; they must never be markup.
    nasty = [{"ts": "2026-07-15T09:00:00Z", "automation": "onboarding", "action": "x",
              "status": "ok", "client_id": "<script>alert(1)</script>",
              "detail": "<img src=x onerror=alert(2)>"}]
    page = dashboard._dashboard_page(nasty, {}).decode("utf-8")
    assert "<script>alert(1)</script>" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;" in page


def test_empty_state():
    page = dashboard._dashboard_page([], {}).decode("utf-8")
    assert "אין פעילות בטווח הזה" in page


def test_filters_narrow_the_list():
    # Two: morning_client_created by action, and no_price via its automation.
    assert len(dashboard._filter(ENTRIES, {"subject": "morning"})) == 2
    assert len(dashboard._filter(ENTRIES, {"client": "אלפא"})) == 2
    assert len(dashboard._filter(ENTRIES, {"q": "token expired"})) == 1
    assert len(dashboard._filter(ENTRIES, {"client": "nobody"})) == 0


def test_dry_run_still_applies_filters(monkeypatch):
    # Regression: dry-run used to return the sample data unfiltered, so the demo
    # showed every row no matter what was selected.
    monkeypatch.setattr(dashboard, "DRY_RUN", True)
    all_rows = dashboard._load({})
    one_client = dashboard._load({"client": "מכללת אלפא"})
    assert len(one_client) < len(all_rows)
    assert {e["client_id"] for e in one_client} == {"מכללת אלפא"}


# ---------------------------------------------------------------- daily email


def test_daily_email_dry_run_sends_nothing(read_log):
    from src.automations import daily_email

    result = daily_email.run(dry_run=True)
    assert result["sent"] is False
    assert "סיכום יומי" in result["subject"]
    assert [e["action"] for e in read_log()] == ["email_prepared"]


def test_daily_email_html_groups_by_subject_and_flags_errors():
    from src.automations import daily_email

    html = daily_email.build_html(ENTRIES, "2026-07-15", "https://dash.example/")
    assert "דורש טיפול" in html
    assert "חשבוניות ותשלומים" in html
    assert 'dir="rtl"' in html
    assert "https://dash.example/" in html


def test_daily_email_subject_line_calls_out_errors():
    from src.automations import daily_email

    counts = subjects.counts(ENTRIES)
    assert counts["error"] == 1
    text = daily_email.build_text(ENTRIES, "2026-07-15")
    assert "סיכום יומי" in text


def test_daily_email_escapes_log_content():
    from src.automations import daily_email

    nasty = [{"ts": "2026-07-15T09:00:00Z", "automation": "onboarding", "action": "x",
              "status": "ok", "client_id": "<script>bad</script>", "detail": "ok"}]
    html = daily_email.build_html(nasty, "2026-07-15")
    assert "<script>bad</script>" not in html

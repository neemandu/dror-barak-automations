"""Tests for signature reminders.

The failure that costs money here is the wrong cadence: chasing a prospect too
hard, or reminding a client who already signed. So the tests care most about
*when* a reminder fires and *when it stops*.
"""

from __future__ import annotations

import time

import pytest

from src.automations import sign_reminders
from src.lib import signing


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "i.json"))
    monkeypatch.delenv("IDEMPOTENCY_TABLE", raising=False)
    yield


DAY = 24 * 60 * 60


def pending(issued_days_ago: float, reminders_sent: int = 0):
    return {"issued_at": int(time.time() - issued_days_ago * DAY),
            "reminders_sent": reminders_sent}


# --------------------------------------------------------- the cadence


def test_no_reminder_before_two_days():
    assert sign_reminders._due(pending(1), time.time()) is None


def test_first_reminder_at_two_days():
    assert sign_reminders._due(pending(2), time.time()) == 1


def test_second_reminder_at_four_days():
    assert sign_reminders._due(pending(4, reminders_sent=1), time.time()) == 2


def test_no_third_reminder_ever():
    # Two nudges, then stop. Chasing a prospect harder loses the deal.
    assert sign_reminders._due(pending(10, reminders_sent=2), time.time()) is None


def test_a_missed_day_does_not_send_two_reminders_at_once():
    # The job didn't run for a while; the client is now 5 days in with none sent.
    # They should get ONE nudge (#2), not #1 and #2 back to back.
    assert sign_reminders._due(pending(5, reminders_sent=0), time.time()) == 2


def test_already_reminded_today_is_not_reminded_again():
    # #1 sent, only 2 days in — #2 isn't due until day 4.
    assert sign_reminders._due(pending(2, reminders_sent=1), time.time()) is None


# ----------------------------------------------------- the whole run


def test_pending_lifecycle():
    signing.mark_pending("c1")
    rec = signing.get_pending("c1")
    assert rec["client_id"] == "c1" and rec["reminders_sent"] == 0

    signing.bump_reminders("c1", 1)
    assert signing.get_pending("c1")["reminders_sent"] == 1

    # Signing clears it — nothing left to chase.
    signing.clear_pending("c1")
    assert signing.get_pending("c1") is None


def test_run_reminds_an_overdue_client(read_log, monkeypatch):
    from src.lib import emails
    from src.lib.clients.crm import CrmClient

    monkeypatch.setattr(CrmClient, "list_by_sub_status", lambda self, s: [
        {"id": "c1", "name": "מכללת אלפא", "email": "a@b.co"}])
    signing.mark_pending("c1")
    # Backdate the record to 3 days ago.
    signing._put_pending("signpending:c1",
                         {"client_id": "c1", "issued_at": int(time.time() - 3 * DAY),
                          "reminders_sent": 0})
    sent = []
    monkeypatch.setattr(emails, "send_template", lambda name, to, **k: sent.append((name, to)))

    result = sign_reminders.run(dry_run=True)
    assert result["reminded"] == 1
    assert sent == [("sign_reminder", "a@b.co")]


def test_a_client_with_no_pending_record_is_skipped_not_guessed(read_log, monkeypatch):
    from src.lib.clients.crm import CrmClient

    monkeypatch.setattr(CrmClient, "list_by_sub_status", lambda self, s: [
        {"id": "c9", "name": "בטא", "email": "x@y.co"}])
    # No mark_pending for c9.
    result = sign_reminders.run(dry_run=True)
    assert result["reminded"] == 0
    assert "no_pending_record" in {e["action"] for e in read_log()}


def test_one_failure_does_not_stop_the_rest(read_log, monkeypatch):
    from src.lib import emails
    from src.lib.clients.crm import CrmClient

    monkeypatch.setattr(CrmClient, "list_by_sub_status", lambda self, s: [
        {"id": "c1", "name": "א", "email": "a@b.co"},
        {"id": "c2", "name": "ב", "email": "c@d.co"}])
    for cid in ("c1", "c2"):
        signing._put_pending(f"signpending:{cid}",
                             {"client_id": cid, "issued_at": int(time.time() - 3 * DAY),
                              "reminders_sent": 0})
    calls = []

    def flaky(name, to, **k):
        calls.append(to)
        if len(calls) == 1:
            raise emails.EmailError("SMTP down")

    monkeypatch.setattr(emails, "send_template", flaky)
    result = sign_reminders.run(dry_run=True)
    assert len(calls) == 2, "the second client must still be chased"
    assert "reminder_failed" in {e["action"] for e in read_log()}

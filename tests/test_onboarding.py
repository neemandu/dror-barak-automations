"""Tests for onboarding (T5) — the step that turns a signed client into a working one.

What goes wrong here is quiet rather than loud: a client who is never promoted to
`לקוח פעיל` gets no monthly report and nobody notices for a month; a template that
fails to copy takes the questionnaire down with it; a rerun makes a second copy of
everything. So these tests care about promotion, isolation between steps, and
what a second run does.
"""

from __future__ import annotations

import pytest

from src.automations import onboarding
from src.lib.clients import crm as crm_mod
from src.lib.clients.crm import STATUS_ACTIVE, SUB_IN_WORK, CrmClient
from src.lib.clients.google import GoogleClient


@pytest.fixture
def captured(monkeypatch):
    """Capture what onboarding writes back, without touching a network."""
    state: dict[str, list] = {"fields": [], "comments": [], "copies": []}

    monkeypatch.setattr(
        CrmClient, "update_fields",
        lambda self, client_id, **fields: state["fields"].append(fields) or {})
    monkeypatch.setattr(
        CrmClient, "append_automation_log",
        lambda self, client_id, message: state["comments"].append(message) or {})
    monkeypatch.setattr(
        GoogleClient, "copy_file",
        lambda self, file_id, new_name, parent_id, **kw: state["copies"].append(new_name)
        or {"id": "copy", "name": new_name})
    return state


def templates_picked(monkeypatch, *names, library=("מעקב פאנלים.xlsx", "תוכנית תוכן.docx"),
                     present=()):
    """Dror ticked ``names`` in the client's תבניות field; the library holds ``library``."""
    from src.automations import client_templates

    monkeypatch.setenv("DRIVE_TEMPLATES_FOLDER_ID", "TPL")
    monkeypatch.setattr(client_templates, "picked_for", lambda cid, dry_run=False: list(names))

    def list_folder(self, parent_id):
        if parent_id == "TPL":
            return [{"id": f"t{i}", "name": n, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
                    for i, n in enumerate(library)]
        return [{"id": f"p{i}", "name": n, "mimeType": "application/vnd.google-apps.spreadsheet"}
                for i, n in enumerate(present)]

    monkeypatch.setattr(GoogleClient, "list_folder", list_folder)


def client_says(monkeypatch, **overrides):
    """Make the CRM return the standard fixture client with fields overridden."""
    def _get(self, client_id):
        return {**crm_mod._fixture_client(client_id), **overrides}

    monkeypatch.setattr(CrmClient, "get_client", _get)


def _written(fields: list[dict]) -> dict:
    merged: dict = {}
    for f in fields:
        merged.update(f)
    return merged


# ------------------------------------------------------------- promotion


def test_onboarding_promotes_the_client_to_active(captured):
    # The bug this pins: only the secondary status used to be advanced, so the
    # client stayed a `ליד` — and list_active_clients, which the monthly campaign
    # report iterates, never saw them again.
    onboarding.run("42", dry_run=True)
    written = _written(captured["fields"])
    assert written["status"] == STATUS_ACTIVE
    assert written["sub_status"] == SUB_IN_WORK


def test_promotion_happens_even_when_the_client_has_no_email(captured, monkeypatch, read_log):
    client_says(monkeypatch, email="")
    onboarding.run("42", dry_run=True)

    assert _written(captured["fields"])["status"] == STATUS_ACTIVE
    entry = next(e for e in read_log() if e["action"] == "no_email")
    # An error, not a skip: nothing else chases a questionnaire that never went out.
    assert entry["status"] == "error"
    assert any("מייל" in c for c in captured["comments"])


# ------------------------------------------------------------- templates


def test_only_the_templates_dror_picked_are_copied(captured, monkeypatch):
    templates_picked(monkeypatch, "מעקב פאנלים")
    onboarding.run("42", dry_run=True)
    assert captured["copies"] == ["מעקב פאנלים - מכללת דוגמה"], "not the whole library"


def test_nothing_picked_copies_nothing(captured, monkeypatch):
    templates_picked(monkeypatch)
    onboarding.run("42", dry_run=True)
    assert captured["copies"] == []


def test_a_template_already_in_the_folder_is_not_copied_twice(captured, monkeypatch):
    templates_picked(monkeypatch, "מעקב פאנלים", "תוכנית תוכן",
                     present=("מעקב פאנלים - מכללת דוגמה",))
    onboarding.run("42", dry_run=True)
    assert captured["copies"] == ["תוכנית תוכן - מכללת דוגמה"]


def test_a_template_failure_does_not_lose_the_questionnaire(captured, monkeypatch, read_log):
    from src.automations import client_templates

    monkeypatch.setattr(client_templates, "picked_for",
                        lambda cid, dry_run=False: (_ for _ in ()).throw(RuntimeError("ClickUp 503")))
    result = onboarding.run("42", dry_run=True)
    assert "template_copy_failed" in {e["action"] for e in read_log()}
    assert result["questionnaire_sent"] is True
    assert _written(captured["fields"])["status"] == STATUS_ACTIVE


def test_the_client_gets_their_folder_at_onboarding(captured, monkeypatch, read_log):
    from src.lib import client_folder

    shared = []
    monkeypatch.setattr(client_folder, "share_with_client",
                        lambda folder_id, email, dry_run=False: shared.append((folder_id, email)) or True)
    result = onboarding.run("42", dry_run=True)
    assert shared and shared[0][1] == result["shared_with"]
    assert "folder_shared" in {e["action"] for e in read_log()}
    assert any("שותפה" in c for c in captured["comments"])


def test_no_email_means_no_share_but_onboarding_goes_on(captured, monkeypatch, read_log):
    client_says(monkeypatch, email="")
    result = onboarding.run("42", dry_run=True)
    assert result["shared_with"] is None
    assert "folder_not_shared" in {e["action"] for e in read_log()}
    assert _written(captured["fields"])["status"] == STATUS_ACTIVE


# ------------------------------------------------------------- folder shape


def test_the_folder_gets_its_subfolders_and_the_recordings_path(captured, read_log):
    result = onboarding.run("42", dry_run=True)
    assert set(result["subfolders"]) == set(
        ["חוזים", "אסטרטגיה", "דוחות קמפיין", "הקלטות"])
    assert _written(captured["fields"])["recordings_path"].startswith("https://")
    assert "subfolders_created" in {e["action"] for e in read_log()}


def test_a_drive_failure_on_subfolders_does_not_stop_onboarding(captured, monkeypatch, read_log):
    from src.lib import client_folder

    def boom(*a, **k):
        raise RuntimeError("Drive 500")

    monkeypatch.setattr(client_folder, "ensure_subfolders", boom)
    result = onboarding.run("42", dry_run=True)

    assert "subfolders_failed" in {e["action"] for e in read_log()}
    assert result["questionnaire_sent"] is True
    assert _written(captured["fields"])["status"] == STATUS_ACTIVE


# ------------------------------------------------------------- Meta + summary


def test_a_missing_meta_ad_account_is_flagged_now_not_in_five_weeks(
        captured, monkeypatch, read_log):
    client_says(monkeypatch, meta_ad_account="")
    onboarding.run("42", dry_run=True)

    entry = next(e for e in read_log() if e["action"] == "meta_account_missing")
    assert entry["status"] == "skipped"
    assert any("Meta" in c for c in captured["comments"])


def test_a_client_with_an_ad_account_is_not_nagged_about_meta(captured, read_log):
    onboarding.run("42", dry_run=True)  # the fixture client has one
    actions = {e["action"] for e in read_log()}
    assert "meta_account_present" in actions
    assert "meta_account_missing" not in actions


def test_onboarding_leaves_a_summary_on_the_clickup_task(captured):
    onboarding.run("42", dry_run=True)
    summary = next(c for c in captured["comments"] if c.startswith("✅"))
    assert "drive.google.com" in summary, "Dror opens the folder from the task"
    assert "לקוח פעיל" in summary


# ---------------------------------------------------------- whatsapp welcome


def test_no_welcome_flow_is_a_visible_skip_not_silence(captured, read_log):
    onboarding.run("42", dry_run=True)
    entry = next(e for e in read_log() if e["action"] == "no_welcome_flow")
    assert entry["status"] == "skipped"
    assert "MANYCHAT_FLOW_ONBOARDING" in entry["detail"]


def test_a_configured_welcome_flow_is_sent_after_the_questionnaire(captured, monkeypatch, read_log):
    monkeypatch.setenv("MANYCHAT_FLOW_ONBOARDING", "content20260101000000_1")
    result = onboarding.run("42", dry_run=True)
    assert result["welcome_flow"] is True
    actions = [e["action"] for e in read_log()]
    assert "welcome_flow_sent" in actions
    assert actions.index("questionnaire_sent") < actions.index("welcome_flow_sent")

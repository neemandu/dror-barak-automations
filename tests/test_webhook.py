"""Tests for the webhook dispatch routing (in dry-run)."""

from __future__ import annotations

import pytest

from src import webhook_server
from src.lib.clients.crm import SUB_INITIAL_MEETING, SUB_SIGNED


@pytest.fixture(autouse=True)
def _dry(monkeypatch):
    monkeypatch.setattr(webhook_server, "DRY_RUN", True)


def test_new_lead_route():
    result = webhook_server._dispatch("/crm/new-lead", {"client_id": "42"})
    assert result["contact"]["resourceName"] == "people/mock"


def test_status_route_initial_meeting():
    result = webhook_server._dispatch(
        "/crm/status", {"client_id": "42", "sub_status": SUB_INITIAL_MEETING}
    )
    assert result["message"]["idMessage"]


def test_status_route_signed_triggers_onboarding():
    result = webhook_server._dispatch(
        "/crm/status", {"client_id": "42", "sub_status": SUB_SIGNED}
    )
    assert result["folder"]["id"] == "drive-folder-mock"


def test_status_route_unknown_substatus_ignored():
    result = webhook_server._dispatch(
        "/crm/status", {"client_id": "42", "sub_status": "something_else"}
    )
    assert "ignored" in result


def test_the_retired_fillout_route_is_gone():
    # Fillout was replaced by our own signing page, which finalises the contract
    # itself -- there is no signature webhook to receive any more.
    import pytest

    with pytest.raises(KeyError):
        webhook_server._dispatch("/fillout/signed", {"client_id": "42"})


def test_clickup_route():
    result = webhook_server._dispatch("/clickup/task", {"task_id": "abc"})
    assert result["dispatched"] is False


def test_unknown_route_raises():
    with pytest.raises(KeyError):
        webhook_server._dispatch("/nope", {})

"""The account-assignment diff — pure, so it needs no live Meta calls."""

from __future__ import annotations

from src.tools.sync_meta_accounts import accounts_to_assign


def test_only_unassigned_accounts_are_returned():
    reachable = [
        {"account_id": "111", "name": "A"},
        {"account_id": "222", "name": "B"},
        {"account_id": "333", "name": "C"},
    ]
    assigned = {"111", "333"}
    todo = accounts_to_assign(reachable, assigned)
    assert [a["account_id"] for a in todo] == ["222"]


def test_nothing_to_do_when_all_assigned():
    reachable = [{"account_id": "111", "name": "A"}]
    assert accounts_to_assign(reachable, {"111"}) == []


def test_everything_when_none_assigned():
    reachable = [{"account_id": "111", "name": "A"}, {"account_id": "222", "name": "B"}]
    assert len(accounts_to_assign(reachable, set())) == 2

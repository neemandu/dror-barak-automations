"""The questionnaire store: seeding, optimistic saves, responses."""

from __future__ import annotations

import pytest

from src.lib import questionnaire as qn
from src.lib import questionnaire_store as store


def test_the_first_read_seeds_the_default_questionnaire():
    defs = store.list_definitions()
    assert [d["id"] for d in defs] == ["strategy"]
    assert store.default_id() == "strategy"


def test_a_save_against_a_stale_version_is_refused():
    defn = store.get_definition("strategy")
    store.save_definition(defn, expected_version=defn["version"])
    with pytest.raises(store.Conflict):
        store.save_definition(defn, expected_version=defn["version"])  # someone saved in between


def test_an_invalid_definition_is_refused_with_reasons():
    defn = store.get_definition("strategy")
    defn["sections"] = []
    with pytest.raises(store.Invalid) as exc:
        store.save_definition(defn, expected_version=defn["version"])
    assert any("חלק" in e for e in exc.value.errors)


def test_copies_get_new_ids_and_the_default_cannot_be_deleted():
    copy = store.create_definition("", copy_from="strategy")
    assert copy["id"] != "strategy" and "(עותק)" in copy["title"]
    with pytest.raises(store.Invalid):
        store.delete_definition("strategy")
    store.delete_definition(copy["id"])
    assert store.get_definition(copy["id"]) is None


def test_responses_keep_the_questions_as_answered():
    defn = store.get_definition("strategy")
    store.record_sent("c9", "מכללה", "strategy")
    store.record_answer("c9", "מכללה", defn, {"business_name": "אלפא"}, doc_url="https://docs/x")
    defn["sections"][0]["questions"][0]["label"] = "נוסח חדש"
    store.save_definition(defn, expected_version=defn["version"])

    r = store.get_response("c9", "strategy")
    assert r["status"] == "answered" and r["doc_url"] == "https://docs/x"
    assert r["snapshot"][0]["label"] == "שם העסק / המותג", "the old wording is what was answered"
    assert [h["event"] for h in r["history"]] == ["sent:email", "answered"]
    assert store.list_responses("strategy")[0]["client_id"] == "c9"

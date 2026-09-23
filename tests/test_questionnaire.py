"""The questionnaire: its model, the client's form, and what a submission sets off.

What matters: a question's key never changes, so editing wording can't orphan
answers; required answers aren't skippable; profile links are found by role, not
wording; and a submission is stored with the questions it answered.
"""

from __future__ import annotations

import copy

import pytest

from src.lib import questionnaire as qn
from src.lib import questionnaire_store as store
from src.lib import signing


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "i.json"))
    monkeypatch.setenv("IDEMPOTENCY_TABLE", "")
    yield


def _full_answers(defn):
    out = {}
    for _s, q in qn.questions(defn):
        if q["kind"] in ("choice", "multi"):
            out[q["key"]] = [q["options"][0]]
        elif q["kind"] == "url":
            out[q["key"]] = ["https://example.com/x"]
        elif q["kind"] == "email":
            out[q["key"]] = ["a@b.co"]
        elif q["kind"] == "scale":
            out[q["key"]] = ["3"]
        else:
            out[q["key"]] = ["תשובה"]
    return out


# ---------------------------------------------------------------- model


def test_the_default_questionnaire_is_valid_and_marks_its_social_links():
    defn = qn.default_definition()
    assert qn.validate(defn) == []
    roles = {q["role"] for _s, q in qn.questions(defn) if q["role"]}
    assert {"instagram", "tiktok", "facebook", "youtube", "website"} <= roles


def test_validation_names_the_problem_in_hebrew():
    defn = qn.default_definition()
    defn["sections"][0]["questions"][1]["key"] = defn["sections"][0]["questions"][0]["key"]
    defn["sections"][1]["questions"].append({"key": "pick", "label": "בחר", "kind": "choice", "options": ["רק אחת"]})
    defn["title"] = ""
    errors = qn.validate(qn.normalize(defn))
    assert any("כותרת" in e for e in errors)
    assert any("כבר בשימוש" in e for e in errors)
    assert any("שתי אפשרויות" in e for e in errors)


def test_two_questions_cannot_both_be_the_instagram_link():
    defn = qn.default_definition()
    defn["sections"][3]["questions"][1]["role"] = "instagram"
    assert any("אינסטגרם" in e for e in qn.validate(defn))


def test_role_only_sticks_to_links():
    q = qn.normalize({"title": "t", "sections": [{"title": "s", "questions": [
        {"key": "a1", "label": "x", "kind": "text", "role": "instagram"}]}]})
    assert q["sections"][0]["questions"][0]["role"] == ""


def test_required_and_format_problems_are_reported_per_question():
    defn = qn.default_definition()
    answers = qn.parse_answers(defn, _full_answers(defn))
    assert qn.problems(defn, answers) == {}
    answers["business_name"] = ""
    answers["instagram"] = "not a link"
    problems = qn.problems(defn, answers)
    assert problems["business_name"] == "שדה חובה"
    assert problems["instagram"] == "קישור לא תקין"


def test_a_bare_domain_becomes_a_link():
    defn = qn.default_definition()
    answers = qn.parse_answers(defn, {"instagram": ["instagram.com/alpha"]})
    assert answers["instagram"] == "https://instagram.com/alpha"


def test_profiles_are_found_by_role_not_by_wording():
    defn = qn.default_definition()
    defn["sections"][3]["questions"][0]["label"] = "איפה אתם באינסטגרם?"   # reworded
    answers = {"instagram": "https://instagram.com/x", "tiktok": "  ", "youtube": "https://youtube.com/@y"}
    assert qn.social_profiles(qn.snapshot(defn), answers) == {
        "instagram": "https://instagram.com/x", "youtube": "https://youtube.com/@y"}


def test_multi_choice_and_scale_answers():
    defn = {"id": "t", "title": "t", "sections": [{"id": "s", "title": "s", "questions": [
        {"key": "chan", "label": "ערוצים", "kind": "multi", "options": ["וובינר", "מייל", "וואטסאפ"]},
        {"key": "sat", "label": "שביעות רצון", "kind": "scale", "scale_max": 5, "required": True}]}]}
    defn = qn.normalize(defn)
    answers = qn.parse_answers(defn, {"chan": ["וובינר", "מייל", "זדוני"], "sat": ["9"]})
    assert answers["chan"] == ["וובינר", "מייל"], "values that are not options are dropped"
    assert qn.problems(defn, answers) == {"sat": "צריך לבחור ערך בסולם"}
    assert qn.display(answers["chan"]) == "וובינר, מייל"


def test_the_document_omits_blanks_and_escapes_client_input():
    defn = qn.default_definition()
    answers = {"business_name": "<script>alert(1)</script>", "goals": "צמיחה"}
    doc = qn.to_document_html("שאלון", "c", qn.snapshot(defn), answers)
    assert "&lt;script&gt;" in doc and "<script>" not in doc
    assert "צמיחה" in doc and "שוק ומתחרים" not in doc


# ---------------------------------------------------------------- the form


def test_the_link_carries_the_client_and_points_at_the_form():
    url = signing.questionnaire_url("c1")
    assert url.startswith("https://sign.example/dev/questionnaire?t=")
    assert signing.resolve(url.split("t=")[1]) == "c1"


def test_get_renders_every_question_of_the_stored_questionnaire():
    from src import questionnaire_page

    defn = store.get_definition(store.default_id())
    page = questionnaire_page.handle_get(signing.make_token("c1"), dry_run=True)
    assert "<form" in page and defn["title"] in page
    for _s, q in qn.questions(defn):
        assert q["label"] in page


def test_the_form_follows_edits_to_the_questionnaire():
    from src import questionnaire_page

    defn = copy.deepcopy(store.get_definition(store.default_id()))
    defn["sections"][0]["questions"][0]["label"] = "איך קוראים לעסק?"
    store.save_definition(defn, expected_version=defn["version"])
    page = questionnaire_page.handle_get(signing.make_token("c1"), dry_run=True)
    assert "איך קוראים לעסק?" in page


def test_missing_required_answers_re_render_with_the_fields_marked():
    from src import questionnaire_page

    defn = store.get_definition(store.default_id())
    form = _full_answers(defn)
    form["business_name"] = [""]
    out = questionnaire_page.handle_post(signing.make_token("c1"), form, dry_run=True)
    assert "דורש תיקון" in out and "has-err" in out
    assert store.latest_answered("c1") is None


def test_a_submission_is_stored_with_its_questions_and_starts_the_analysis(monkeypatch):
    from src import questionnaire_page
    from src.lib import tasks

    started = []
    monkeypatch.setattr(tasks, "dispatch", lambda name, **kw: started.append((name, kw)))
    defn = store.get_definition(store.default_id())
    form = _full_answers(defn)
    form["instagram"] = ["https://instagram.com/alpha"]
    out = questionnaire_page.handle_post(signing.make_token("c1"), form, dry_run=True)

    assert "תודה" in out
    saved = store.latest_answered("c1")
    assert saved["answers"]["instagram"] == "https://instagram.com/alpha"
    assert {q["key"] for q in saved["snapshot"]} == {q["key"] for _s, q in qn.questions(defn)}
    assert started == [("social_prep", {"client_id": "c1", "dry_run": True})]


def test_a_second_visit_shows_the_previous_answers():
    from src import questionnaire_page

    defn = store.get_definition(store.default_id())
    questionnaire_page.handle_post(signing.make_token("c1"), _full_answers(defn), dry_run=True)
    page = questionnaire_page.handle_get(signing.make_token("c1"), dry_run=True)
    assert "כבר מילאת את השאלון" in page


def test_a_client_keeps_the_questionnaire_they_were_sent():
    other = store.create_definition("שאלון קצר")
    store.record_sent("c1", "לקוח", other["id"])
    store.set_default(store.create_definition("שאלון אחר")["id"])
    assert store.definition_for_client("c1")["id"] == other["id"]


def test_a_bad_link_is_refused():
    from src import questionnaire_page

    with pytest.raises(signing.SigningError):
        questionnaire_page.handle_get("forged", dry_run=True)


def test_the_preview_never_submits():
    from src import questionnaire_page

    page = questionnaire_page.preview_page(store.get_definition(store.default_id()))
    assert "data-preview" in page and 'action="#"' in page and "תצוגה מקדימה" in page

"""Tests for the strategy questionnaire — our own form.

The two things that matter: required answers aren't skippable, and the profile
links the client gives actually reach the social-media analysis.
"""

from __future__ import annotations

import pytest

from src.lib import questionnaire, signing


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGN_LINK_SECRET", "test-secret")
    monkeypatch.setenv("SIGN_BASE_URL", "https://sign.example/dev")
    monkeypatch.setenv("IDEMPOTENCY_PATH", str(tmp_path / "i.json"))
    monkeypatch.delenv("IDEMPOTENCY_TABLE", raising=False)
    yield


# --------------------------------------------------------- content


def test_required_questions_are_enforced():
    answers = {q.key: "x" for q in questionnaire.all_questions()}
    assert questionnaire.missing(answers) == []
    answers["business_name"] = ""
    assert "שם העסק / המותג" in questionnaire.missing(answers)


def test_social_profiles_are_pulled_for_analysis():
    answers = {"instagram": "https://instagram.com/x", "tiktok": "  ",
               "youtube": "https://youtube.com/@y", "goals": "grow"}
    profiles = questionnaire.social_profiles(answers)
    assert profiles == {"instagram": "https://instagram.com/x",
                        "youtube": "https://youtube.com/@y"}


def test_the_document_omits_blank_answers():
    answers = {"business_name": "מכללת אלפא", "goals": "צמיחה"}
    doc = questionnaire.to_document_html("מכללת אלפא", answers)
    assert "מכללת אלפא" in doc and "צמיחה" in doc
    # A section with no answers is not rendered as empty headers.
    assert "שוק ומתחרים" not in doc


def test_the_document_escapes_client_input():
    answers = {"business_name": "<script>alert(1)</script>", "goals": "x"}
    doc = questionnaire.to_document_html("c", answers)
    assert "<script>" not in doc
    assert "&lt;script&gt;" in doc


# --------------------------------------------------------- page


def test_the_link_carries_the_client_and_points_at_the_form():
    url = signing.questionnaire_url("c1")
    assert url.startswith("https://sign.example/dev/questionnaire?t=")
    code = url.split("t=")[1]
    assert signing.resolve(code) == "c1"


def test_get_renders_the_form():
    from src import questionnaire_page

    page = questionnaire_page.handle_get(signing.make_short_code("c1"), dry_run=True)
    assert "שאלון הכנה לבניית אסטרטגיה" in page
    assert "<form" in page
    for q in questionnaire.all_questions():
        assert q.label in page


def test_post_missing_a_required_field_re_renders_with_an_error():
    from src import questionnaire_page

    token = signing.make_short_code("c1")
    form = {q.key: ["x"] for q in questionnaire.all_questions()}
    form["business_name"] = [""]  # required, blank
    out = questionnaire_page.handle_post(token, form, dry_run=True)
    assert "חסרים שדות חובה" in out
    assert "שם העסק" in out


def test_a_complete_submission_shows_thanks_and_feeds_social(monkeypatch):
    from src import questionnaire_page
    from src.automations import social_prep

    fed = {}
    monkeypatch.setattr(social_prep, "run",
                        lambda cid, dry_run=False, profiles=None: fed.update(cid=cid, profiles=profiles))

    token = signing.make_short_code("c1")
    form = {q.key: ["x"] for q in questionnaire.all_questions()}
    form["instagram"] = ["https://instagram.com/alpha"]
    out = questionnaire_page.handle_post(token, form, dry_run=True)

    assert "תודה" in out
    assert fed["cid"] == "c1"
    assert fed["profiles"]["instagram"] == "https://instagram.com/alpha"


def test_a_bad_link_is_refused():
    from src import questionnaire_page

    with pytest.raises(signing.SigningError):
        questionnaire_page.handle_get("forged", dry_run=True)

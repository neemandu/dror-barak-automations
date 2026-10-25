"""Instagram and YouTube read from their official APIs for the social prep report."""

from __future__ import annotations

import pytest

from src.automations import social_prep
from src.lib import social_sources
from src.lib.http import HttpError


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-token")
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key")
    monkeypatch.setattr(social_sources, "_caller", None)


class _Resp:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def _api(monkeypatch, answer):
    """Fake HTTP: ``answer(url, params)`` returns the JSON body (or raises)."""
    calls = []

    def request(method, url, **kw):
        calls.append((url, kw.get("params", {})))
        return _Resp(answer(url, kw.get("params", {})))

    monkeypatch.setattr(social_sources, "request", request)
    return calls


@pytest.mark.parametrize("url,name", [
    ("https://www.instagram.com/alpha.college/?hl=he", "alpha.college"),
    ("instagram.com/alpha", "alpha"),
    ("@alpha", "alpha"),
    ("https://www.instagram.com/p/Cx1/", ""),  # a post, not a profile
])
def test_the_username_comes_out_of_any_profile_link(url, name):
    assert social_sources.instagram_username(url) == name


def test_instagram_profile_and_recent_posts_through_business_discovery(monkeypatch):
    def answer(url, params):
        if url.endswith("/me/accounts"):
            return {"data": [{"id": "p1"}, {"id": "p2", "instagram_business_account": {"id": "IG9"}}]}
        assert url.endswith("/IG9") and "business_discovery.username(alpha)" in params["fields"]
        return {"business_discovery": {
            "username": "alpha", "name": "מכללת אלפא", "biography": "קורסים\nלמבוגרים",
            "followers_count": 5400, "follows_count": 80, "media_count": 312,
            "media": {"data": [{"timestamp": "2026-10-20T08:00:00+0000", "media_product_type": "REELS",
                                "like_count": 140, "comments_count": 12, "permalink": "https://instagram.com/p/1",
                                "caption": "וובינר חינם ביום שלישי"}]}}}

    calls = _api(monkeypatch, answer)
    text = social_sources.profile_data("instagram", "https://www.instagram.com/alpha/")
    assert "עוקבים: 5400" in text and "פוסטים: 312" in text and "קורסים למבוגרים" in text
    assert "2026-10-20 | REELS | 140 | 12" in text and "וובינר חינם" in text
    social_sources.profile_data("instagram", "https://www.instagram.com/alpha/")
    assert sum(u.endswith("/me/accounts") for u, _ in calls) == 1, "our account is looked up once"


def test_without_our_instagram_account_it_says_what_is_missing(monkeypatch):
    _api(monkeypatch, lambda url, params: {"data": [{"id": "p1"}]})
    with pytest.raises(social_sources.Unavailable, match="הגדרה חד-פעמית"):
        social_sources.instagram("https://instagram.com/alpha")


def test_a_personal_instagram_account_is_said_plainly(monkeypatch):
    def answer(url, params):
        if url.endswith("/me/accounts"):
            return {"data": [{"instagram_business_account": {"id": "IG9"}}]}
        raise HttpError(400, url, '{"error":{"code":110,"message":"Invalid user id"}}')

    _api(monkeypatch, answer)
    with pytest.raises(social_sources.Unavailable, match="חשבון אישי"):
        social_sources.instagram("https://instagram.com/someone")


@pytest.mark.parametrize("url,ref", [
    ("https://www.youtube.com/@AlphaCollege", ("forHandle", "@AlphaCollege")),
    ("https://youtube.com/channel/UCabcdefghijklmnopqrstuv", ("id", "UCabcdefghijklmnopqrstuv")),
    ("https://www.youtube.com/user/alphatv", ("forUsername", "alphatv")),
    ("https://www.youtube.com/c/Alpha", ("search", "Alpha")),
])
def test_the_channel_comes_out_of_any_channel_link(url, ref):
    assert social_sources.youtube_channel_ref(url) == ref


def test_youtube_channel_and_recent_videos(monkeypatch):
    def answer(url, params):
        assert params["key"] == "yt-key"
        if url.endswith("/channels"):
            assert params["forHandle"] == "@Alpha"
            return {"items": [{"snippet": {"title": "אלפא", "customUrl": "@alpha", "description": "ערוץ המכללה"},
                               "statistics": {"subscriberCount": "1200", "videoCount": "40", "viewCount": "90000"},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
        if url.endswith("/playlistItems"):
            return {"items": [{"contentDetails": {"videoId": "v1"}}]}
        return {"items": [{"snippet": {"publishedAt": "2026-10-01T00:00:00Z", "title": "איך בוחרים קורס"},
                           "statistics": {"viewCount": "800", "likeCount": "30", "commentCount": "4"},
                           "contentDetails": {"duration": "PT12M"}}]}

    _api(monkeypatch, answer)
    text = social_sources.profile_data("youtube", "https://www.youtube.com/@Alpha")
    assert "מנויים: 1200" in text and "2026-10-01 | PT12M | 800 | 30 | 4 | איך בוחרים קורס" in text


def test_no_key_means_no_api_call(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY")
    _api(monkeypatch, lambda url, params: pytest.fail("no key, no call"))
    with pytest.raises(social_sources.Unavailable, match="אין מפתח"):
        social_sources.youtube("https://www.youtube.com/@Alpha")


# ------------------------------------------------------------- in the report


class _AI:
    dry_run = False

    def __init__(self):
        self.prompts = []

    def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        return "**מקור המידע:** x"


def test_the_report_hands_claude_the_api_data(monkeypatch):
    monkeypatch.setattr(social_sources, "profile_data", lambda role, url: "עוקבים: 5400")
    ai = _AI()
    social_prep.analyze_profiles({"instagram": "https://instagram.com/alpha"}, ai)
    assert "<נתונים>\nעוקבים: 5400" in ai.prompts[0] and "אין צורך לפתוח את הקישור" in ai.prompts[0]


def test_without_api_data_the_report_reads_the_web_and_says_why(monkeypatch):
    def unavailable(role, url):
        raise social_sources.Unavailable("אין מפתח YouTube API במערכת")

    monkeypatch.setattr(social_sources, "profile_data", unavailable)
    ai = _AI()
    social_prep.analyze_profiles({"youtube": "https://youtube.com/@a"}, ai)
    assert "לא זמינים: אין מפתח YouTube API במערכת" in ai.prompts[0] and "פתח את הקישור" in ai.prompts[0]


def test_a_network_without_an_api_is_read_from_the_web_with_no_excuse(monkeypatch):
    monkeypatch.setattr(social_sources, "profile_data", lambda *a: pytest.fail("no API for TikTok"))
    ai = _AI()
    social_prep.analyze_profiles({"tiktok": "https://tiktok.com/@a"}, ai)
    assert "לא זמינים" not in ai.prompts[0] and "פתח את הקישור" in ai.prompts[0]

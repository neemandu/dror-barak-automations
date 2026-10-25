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
    monkeypatch.setattr(social_sources, "profile_data", lambda *a: pytest.fail("a website is opened, not scraped"))
    ai = _AI()
    social_prep.analyze_profiles({"website": "https://alpha.co.il"}, ai)
    assert "לא זמינים" not in ai.prompts[0] and "פתח את הקישור" in ai.prompts[0]


def test_scraped_data_is_not_called_official(monkeypatch):
    monkeypatch.setattr(social_sources, "profile_data", lambda role, url: "עוקבים: 10")
    ai = _AI()
    social_prep.analyze_profiles({"tiktok": "https://tiktok.com/@a", "instagram": "https://instagram.com/a"}, ai)
    tiktok, instagram = sorted(ai.prompts, key=lambda p: "טיקטוק" not in p)
    assert "מסריקה אוטומטית של הדף הציבורי" in tiktok and "מהממשק הרשמי של הרשת" not in tiktok
    assert "מהממשק הרשמי של הרשת" in instagram


# ----------------------------------------------------- TikTok and Facebook (Apify)


class _Post:
    """Fake ``requests``: ``answer(actor, payload)`` -> (status, JSON body)."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def post(self, url, *, params, json, headers, timeout):
        actor = url.split("/acts/")[1].split("/")[0]
        self.calls.append((actor, json))
        status, body = self.answer(actor, json)
        return type("R", (), {"status_code": status, "json": lambda self: body})()


def _apify(monkeypatch, answer):
    monkeypatch.setenv("APIFY_TOKEN", "apify-token")
    fake = _Post(answer)
    monkeypatch.setattr(social_sources, "_requests", lambda: fake)
    return fake


@pytest.mark.parametrize("url,name", [
    ("https://www.tiktok.com/@alpha.college?lang=he", "alpha.college"),
    ("https://www.tiktok.com/@alpha/video/123", "alpha"),
    ("@alpha", "alpha"),
    ("https://vm.tiktok.com/ZMabc/", ""),  # a short link names no one
])
def test_the_tiktok_username_comes_out_of_any_link(url, name):
    assert social_sources.tiktok_username(url) == name


def test_tiktok_profile_and_videos_newest_first_with_the_pinned_one_marked(monkeypatch):
    author = {"name": "alpha", "nickName": "אלפא", "fans": 900, "following": 10, "heart": 5000,
              "video": 30, "signature": "קורסים", "bioLink": "alpha.co.il"}
    videos = [{"authorMeta": author, "createTime": 1, "createTimeISO": "2026-01-01T00:00:00Z", "isPinned": True,
               "playCount": 9, "diggCount": 1, "commentCount": 0, "shareCount": 0, "webVideoUrl": "u1", "text": "ישן"},
              {"authorMeta": author, "createTime": 2, "createTimeISO": "2026-10-01T00:00:00Z",
               "playCount": 800, "diggCount": 40, "commentCount": 3, "shareCount": 2, "webVideoUrl": "u2", "text": "חדש"}]
    fake = _apify(monkeypatch, lambda actor, payload: (201, videos))
    text = social_sources.profile_data("tiktok", "https://www.tiktok.com/@alpha")
    assert fake.calls == [(social_sources.TIKTOK_ACTOR, {"profiles": ["alpha"], "resultsPerPage": 12})]
    assert "עוקבים: 900" in text and "לייקים בסך הכל: 5000" in text
    assert text.index("2026-10-01 | 800") < text.index("2026-01-01 (נעוץ) | 9")


def test_facebook_page_and_posts(monkeypatch):
    def answer(actor, payload):
        assert payload["startUrls"] == [{"url": "https://www.facebook.com/alpha"}]
        if actor == social_sources.FACEBOOK_PAGE_ACTOR:
            return 201, [{"title": "מכללת אלפא", "categories": ["Education"], "followers": 4200,
                          "intro": "קורסים", "website": "alpha.co.il"}]
        return 201, [{"time": "2026-10-20T10:00:00.000Z", "isVideo": True, "likes": 50, "comments": 4,
                      "shares": 2, "viewsCount": 1000, "url": "p1", "text": "וובינר"}]

    _apify(monkeypatch, answer)
    text = social_sources.profile_data("facebook", "fb.com/alpha")
    assert "דף: מכללת אלפא (Education)" in text and "עוקבים: 4200" in text
    assert "2026-10-20 | וידאו | 50 | 4 | 2 | 1000 | p1 | וובינר" in text


@pytest.mark.parametrize("status,body,why", [
    (201, [], "לא נמצא"),
    (201, [{"error": "not_found"}], "לא נמצא"),
    (402, {}, "נגמר הקרדיט"),
    (400, {}, "נכשלה"),
])
def test_a_failed_scrape_says_why(monkeypatch, status, body, why):
    fake = _apify(monkeypatch, lambda actor, payload: (status, body))
    with pytest.raises(social_sources.Unavailable, match=why):
        social_sources.facebook("https://www.facebook.com/alpha")
    assert len(fake.calls) == 1, "no retry and no posts run after the page failed: each run is billed"


def test_no_apify_token_means_no_scrape(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.setattr(social_sources, "_requests", lambda: pytest.fail("no token, no run"))
    with pytest.raises(social_sources.Unavailable, match="אין טוקן Apify"):
        social_sources.tiktok("https://www.tiktok.com/@alpha")


@pytest.mark.parametrize("url,clean", [
    ("https://il.linkedin.com/company/alpha-college/about/?trk=x", "https://www.linkedin.com/company/alpha-college/"),
    ("linkedin.com/in/dror-barak", "https://www.linkedin.com/in/dror-barak/"),
    ("https://www.linkedin.com/school/alpha/", "https://www.linkedin.com/school/alpha/"),
])
def test_a_linkedin_link_becomes_the_page_itself(url, clean):
    assert social_sources.linkedin_url(url) == clean


@pytest.mark.parametrize("url,why", [
    ("https://www.linkedin.com/posts/alpha_activity-1", "לא לדף חברה"),
    ("https://notlinkedin.com/company/alpha", "לא מוביל ללינקדאין"),
])
def test_a_linkedin_link_that_is_not_a_page_is_not_scraped(url, why):
    with pytest.raises(social_sources.Unavailable, match=why):
        social_sources.linkedin_url(url)


def test_linkedin_company_posts_newest_first_with_reposts_marked(monkeypatch):
    author = {"type": "company", "name": "מכללת אלפא", "info": "4,200 followers"}
    posts = [{"author": {"type": "profile", "name": "דנה"}, "repostedBy": author,
              "postedAt": {"timestamp": 3, "date": "2026-10-05T00:00:00Z"},
              "engagement": {"likes": 9, "comments": 0, "shares": 0}, "linkedinUrl": "l3", "content": "של דנה"},
             {"author": author, "postedAt": {"timestamp": 1, "date": "2026-09-01T00:00:00Z"}, "repost": {"id": "9"},
              "engagement": {"likes": 5, "comments": 0, "shares": 0}, "linkedinUrl": "l1", "content": "ישן"},
             {"author": author, "postedAt": {"timestamp": 2, "date": "2026-10-01T00:00:00Z"},
              "engagement": {"likes": 40, "comments": 3, "shares": 2}, "linkedinUrl": "l2", "content": "פתיחת מחזור"}]
    fake = _apify(monkeypatch, lambda actor, payload: (201, posts))
    text = social_sources.profile_data("linkedin", "https://www.linkedin.com/company/alpha")
    assert fake.calls == [(social_sources.LINKEDIN_ACTOR,
                           {"targetUrls": ["https://www.linkedin.com/company/alpha/"], "maxPosts": 12})]
    assert "חברה: מכללת אלפא" in text and "4,200 followers" in text, "the page, not the author it shared"
    assert "2026-10-05 (שיתוף של פוסט של דנה) | 9" in text
    assert text.index("2026-10-01 | 40 | 3 | 2 | l2 | פתיחת מחזור") < text.index("2026-09-01 (ציטוט של פוסט אחר) | 5")


def test_a_link_outside_facebook_is_not_scraped():
    with pytest.raises(social_sources.Unavailable, match="לא מוביל לפייסבוק"):
        social_sources.facebook_page_url("https://example.com/alpha")

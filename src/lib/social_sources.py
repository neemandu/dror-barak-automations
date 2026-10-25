"""Profile data from the networks' official APIs, for the social prep report (T3).

Instagram and the rest refuse automated visitors: Claude's web fetch gets a login
wall, and the report could only say it saw nothing. Where a network has an
official read API we can use, the data comes from it instead:

* **Instagram**: Meta's Business Discovery (``business_discovery``), which reads
  another business or creator account through one of our own Instagram business
  accounts. Uses ``META_ACCESS_TOKEN`` (the system user's token, which then needs
  ``instagram_basic`` and ``pages_show_list``; docs/CREDENTIALS.md); our account is
  found through the pages the token can see. A personal (non-business) account
  cannot be read this way.
* **YouTube**: the YouTube Data API with ``YOUTUBE_API_KEY``.

:func:`profile_data` returns the data as plain text for the prompt, or raises
:class:`Unavailable` with a reason in Hebrew; the report then falls back to the web
and says why. Facebook, TikTok and LinkedIn have no such API for other accounts.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

from . import config
from .http import HttpError, request

YOUTUBE = "https://www.googleapis.com/youtube/v3"
WITH_API = ("instagram", "youtube")
_RECENT = 12  # posts/videos read per profile


class Unavailable(Exception):
    """No API data for this profile; the message says why (shown in the report)."""


def profile_data(role: str, url: str) -> str:
    """The profile's data from its network's API, as text. Raises :class:`Unavailable`."""
    if role == "instagram":
        return instagram(url)
    if role == "youtube":
        return youtube(url)
    raise Unavailable("לרשת הזו אין ממשק רשמי לקריאת חשבונות של אחרים")


# ------------------------------------------------------------------ Instagram


def instagram_username(url: str) -> str:
    """``https://www.instagram.com/alpha/?hl=he`` -> ``alpha``; a post or reel link -> ''."""
    raw = url.strip()
    if "/" not in raw and "." not in raw:  # "@alpha" or "alpha"
        return raw.lstrip("@")
    parts = [p for p in urlparse(raw if "//" in raw else f"https://{raw}").path.split("/") if p]
    if not parts or parts[0] in ("p", "reel", "reels", "stories", "explore", "tv"):
        return ""
    return parts[0].lstrip("@")


_caller: Optional[str] = None


def _graph(path: str, **params: Any) -> dict[str, Any]:
    token = config.get("META_ACCESS_TOKEN")
    if not token:
        raise Unavailable("אין טוקן Meta במערכת")
    base = str(config.get("META_BASE_URL", "https://graph.facebook.com/v21.0")).rstrip("/")
    return request("GET", f"{base}/{path}", params={**params, "access_token": token}).json()


def _our_instagram_account() -> str:
    """One of our Instagram business accounts, the one Business Discovery reads through."""
    global _caller
    if _caller:
        return _caller
    pages = _graph("me/accounts", fields="instagram_business_account", limit="50").get("data", [])
    for page in pages:
        account = (page.get("instagram_business_account") or {}).get("id")
        if account:
            _caller = str(account)
            return _caller
    raise Unavailable("לטוקן של Meta אין עדיין גישה לחשבון אינסטגרם עסקי שלנו "
                      "(הגדרה חד-פעמית, ראו את מדריך המערכת)")


def instagram(url: str) -> str:
    username = instagram_username(url)
    if not username:
        raise Unavailable("הקישור הוא לפוסט ולא לפרופיל")
    caller = _our_instagram_account()
    fields = (f"business_discovery.username({username})"
              "{username,name,biography,website,followers_count,follows_count,media_count,"
              f"media.limit({_RECENT})"
              "{caption,media_type,media_product_type,like_count,comments_count,timestamp,permalink}}")
    try:
        found = _graph(caller, fields=fields).get("business_discovery") or {}
    except HttpError as exc:
        if exc.status == 400 and ("110" in exc.body or "business" in exc.body.lower()
                                  or "Invalid user id" in exc.body):
            raise Unavailable("החשבון לא נמצא, או שהוא חשבון אישי ולא עסקי, ולכן הממשק של Meta לא חושף אותו") from exc
        raise Unavailable(f"הממשק של Meta החזיר שגיאה ({exc.status})") from exc
    lines = [
        f"חשבון: @{found.get('username', username)} ({found.get('name') or ''})",
        f"עוקבים: {found.get('followers_count', '?')} · עוקב אחרי: {found.get('follows_count', '?')} "
        f"· פוסטים: {found.get('media_count', '?')}",
        f"ביו: {_one_line(found.get('biography'))}",
        f"אתר בביו: {found.get('website') or '-'}",
        "",
        f"{_RECENT} הפוסטים האחרונים (תאריך | סוג | לייקים | תגובות | קישור | כיתוב):",
    ]
    for m in (found.get("media") or {}).get("data", []):
        kind = m.get("media_product_type") or m.get("media_type") or ""
        lines.append(f"- {str(m.get('timestamp', ''))[:10]} | {kind} | {m.get('like_count', '?')} | "
                     f"{m.get('comments_count', '?')} | {m.get('permalink', '')} | {_one_line(m.get('caption'), 300)}")
    return "\n".join(lines)


# -------------------------------------------------------------------- YouTube


_CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")


def youtube_channel_ref(url: str) -> tuple[str, str]:
    """``(kind, value)`` for the channels.list lookup: ``forHandle``, ``id``,
    ``forUsername`` or ``search`` (a legacy /c/ name, found by search)."""
    raw = url.strip()
    if raw.startswith("@"):
        return "forHandle", raw
    parts = [p for p in urlparse(raw if "//" in raw else f"https://{raw}").path.split("/") if p]
    if not parts:
        raise Unavailable("לא זוהה ערוץ בקישור")
    if parts[0].startswith("@"):
        return "forHandle", parts[0]
    if parts[0] == "channel" and len(parts) > 1 and _CHANNEL_ID.match(parts[1]):
        return "id", parts[1]
    if parts[0] == "user" and len(parts) > 1:
        return "forUsername", parts[1]
    if parts[0] == "c" and len(parts) > 1:
        return "search", parts[1]
    if parts[0] in ("watch", "shorts", "playlist", "embed"):
        raise Unavailable("הקישור הוא לסרטון ולא לערוץ")
    return "search", parts[0]


def _yt(path: str, **params: Any) -> dict[str, Any]:
    key = config.get("YOUTUBE_API_KEY")
    if not key:
        raise Unavailable("אין מפתח YouTube API במערכת")
    try:
        return request("GET", f"{YOUTUBE}/{path}", params={**params, "key": key}).json()
    except HttpError as exc:
        raise Unavailable(f"הממשק של YouTube החזיר שגיאה ({exc.status})") from exc


def youtube(url: str) -> str:
    kind, value = youtube_channel_ref(url)
    if kind == "search":
        hits = _yt("search", part="snippet", type="channel", q=value, maxResults="1").get("items", [])
        if not hits:
            raise Unavailable("הערוץ לא נמצא")
        kind, value = "id", hits[0]["snippet"]["channelId"]
    channels = _yt("channels", part="snippet,statistics,contentDetails", **{kind: value}).get("items", [])
    if not channels:
        raise Unavailable("הערוץ לא נמצא")
    ch = channels[0]
    stats, snip = ch.get("statistics", {}), ch.get("snippet", {})
    uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
    ids = [i["contentDetails"]["videoId"] for i in
           _yt("playlistItems", part="contentDetails", playlistId=uploads, maxResults=str(_RECENT)).get("items", [])
           ] if uploads else []
    videos = _yt("videos", part="snippet,statistics,contentDetails", id=",".join(ids)).get("items", []) if ids else []
    lines = [
        f"ערוץ: {snip.get('title', '')} ({snip.get('customUrl', '')})",
        f"מנויים: {stats.get('subscriberCount', 'מוסתר')} · סרטונים: {stats.get('videoCount', '?')} "
        f"· צפיות בסך הכל: {stats.get('viewCount', '?')}",
        f"תיאור: {_one_line(snip.get('description'), 500)}",
        "",
        f"{_RECENT} הסרטונים האחרונים (תאריך | אורך | צפיות | לייקים | תגובות | כותרת):",
    ]
    for v in videos:
        s = v.get("statistics", {})
        lines.append(f"- {str(v['snippet'].get('publishedAt', ''))[:10]} | {v.get('contentDetails', {}).get('duration', '')} | "
                     f"{s.get('viewCount', '?')} | {s.get('likeCount', '?')} | {s.get('commentCount', '?')} | "
                     f"{_one_line(v['snippet'].get('title'), 150)}")
    return "\n".join(lines)


def _one_line(text: Any, limit: int = 400) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "..."

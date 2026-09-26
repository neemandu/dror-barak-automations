"""House style for text the system writes in Dror's name.

Long dashes (— and –) are the tell of machine-written Hebrew: Dror doesn't type
them, and a client who notices them stops believing a person wrote the mail or
the strategy. Our own copy has none (``tests/test_house_style.py`` fails on one),
and everything Claude writes passes through :func:`humanize` on its way out, on
top of :data:`AI_STYLE` asking it not to write them in the first place.
"""

from __future__ import annotations

import re

#: Appended to every system prompt (``AnthropicClient.complete``).
AI_STYLE = (
    "סגנון כתיבה: כתוב כמו אדם, לא כמו מודל שפה. אל תשתמש במקף ארוך (\u2014 או \u2013) בשום מקום; "
    "במקומו פסיק, נקודה או נקודתיים, או פצל למשפט נוסף. בלי אימוג'ים במסמכים ובמיילים; "
    "בפוסטים ובמודעות לרשתות חברתיות רק כשזה מתאים לפלטפורמה או כשהמשימה מבקשת."
)

_RANGE = re.compile(r"(?<=\d)\s*[\u2014\u2013]\s*(?=\d)")      # 2,500–6,000
_LEADING = re.compile(r"(?m)^([ \t]*)[\u2014\u2013][ \t]*")     # a line opening with a dash
_DASH = re.compile(r"[ \t]*[\u2014\u2013][ \t]*")


def humanize(text: str) -> str:
    """Replace long dashes with what a person would type."""
    text = _RANGE.sub("-", text)
    text = _LEADING.sub(r"\1", text)
    return _DASH.sub(" - ", text)

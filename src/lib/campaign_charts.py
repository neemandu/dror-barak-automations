"""Bar-chart PNGs for the campaign report.

The report becomes a PDF through Google Drive's HTML→Docs conversion, which drops
CSS/SVG charts but keeps embedded images. So charts are drawn to PNG here and
embedded as data-URIs, the same way the logo is.

Pillow rather than matplotlib: it is lighter in the Lambda package and gives exact
control over the brand-styled bars. A bundled DejaVu Sans (Latin + Hebrew + ₪ in
one file) is used so campaign names and shekel amounts render on any platform —
Lambda has no system fonts — and python-bidi reorders mixed Hebrew/Latin labels so
they read correctly.

One chart = one measure = one hue (dataviz: a single series needs no legend; the
section heading in the HTML names it). Bars are sorted by magnitude, value labels
sit in dark ink beside each bar so the bar colour stays vivid, and bar ends are
rounded.
"""

from __future__ import annotations

import io
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Optional

from PIL import Image, ImageDraw, ImageFont

# Directional/zero-width marks that clutter Meta's campaign names; strip before
# reordering so a name doesn't start with a stray control glyph.
_BIDI_CONTROLS = re.compile(r"[‎‏‪-‮⁦-⁩]")

_INK = (20, 23, 26)          # near-black, matches the report text
_MUTED = (91, 100, 114)      # secondary ink for the value labels
_TRACK = (238, 240, 243)     # faint bar track (the "empty" remainder)

_FONT_DIR = None  # resolved lazily


def _font_dir() -> Path:
    global _FONT_DIR
    if _FONT_DIR is None:
        here = Path(__file__).resolve()
        for parent in here.parents:
            cand = parent / "templates" / "assets" / "fonts"
            if (cand / "DejaVuSans.ttf").exists():
                _FONT_DIR = cand
                break
        else:
            raise FileNotFoundError("templates/assets/fonts/DejaVuSans.ttf not found")
    return _FONT_DIR


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    # BASIC layout, not RAQM: we reorder Hebrew ourselves with get_display (see
    # _shape), so Pillow must draw glyphs in the exact order given. Left on the
    # default RAQM engine, Pillow would apply its own bidi and double-reverse the
    # text — and RAQM isn't guaranteed present on Lambda, so relying on it would
    # make the charts render differently there. BASIC is deterministic everywhere.
    return ImageFont.truetype(
        str(_font_dir() / name), size, layout_engine=ImageFont.Layout.BASIC
    )


def _shape(text: str) -> str:
    """Clean control marks and reorder for display (RTL-correct)."""
    cleaned = _BIDI_CONTROLS.sub("", text or "").strip()
    cleaned = "".join(c for c in cleaned if unicodedata.category(c) != "Cf")
    try:
        from bidi.algorithm import get_display

        return get_display(cleaned)
    except Exception:  # noqa: BLE001 - a missing bidi lib must not lose the chart
        return cleaned


def bar_chart(
    items: list[dict[str, Any]],
    *,
    value_key: str,
    label_key: str = "name",
    color: tuple[int, int, int],
    fmt: Callable[[float], str],
    width: int = 1240,
    top_n: int = 6,
    scale: int = 2,
) -> bytes:
    """A horizontal bar chart PNG (bytes) for ``items``, biggest first.

    ``width`` is the rendered pixel width (rendered at ``scale``× for crispness;
    display it at ``width/scale`` via the img tag). ``fmt`` formats each value for
    its label (e.g. money → ``"5,095 ₪"``).
    """
    rows = [r for r in items if float(r.get(value_key) or 0) > 0][:top_n]
    if not rows:
        rows = items[:1]  # still draw something rather than an empty image
    biggest = max((float(r.get(value_key) or 0) for r in rows), default=0) or 1.0

    pad = 14 * scale
    row_h = 30 * scale
    gap = 20 * scale
    name_h = 20 * scale
    bar_h = 16 * scale
    radius = bar_h // 2
    value_w = 150 * scale        # reserved for the value label at the bar's end
    label_size = 15 * scale
    value_size = 15 * scale

    plot_w = width - 2 * pad - value_w
    height = pad * 2 + len(rows) * (name_h + row_h + gap)

    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    f_label = _font(label_size)
    f_value = _font(value_size, bold=True)

    y = pad
    for r in rows:
        value = float(r.get(value_key) or 0)
        name = _shape(str(r.get(label_key) or ""))
        # Name above its bar — avoids left/right alignment fights with long,
        # mixed-script campaign names.
        d.text((pad, y), name, font=f_label, fill=_INK)
        y += name_h

        bar_len = int(plot_w * (value / biggest)) if biggest else 0
        bar_len = max(bar_len, radius * 2)  # always a visible rounded nub
        top = y + (row_h - bar_h) // 2
        # Faint full-width track, then the value bar on top of it.
        d.rounded_rectangle([pad, top, pad + plot_w, top + bar_h],
                            radius=radius, fill=_TRACK)
        d.rounded_rectangle([pad, top, pad + bar_len, top + bar_h],
                            radius=radius, fill=color)
        d.text((pad + plot_w + 10 * scale, y + (row_h - value_size) // 2 - 2 * scale),
               fmt(value), font=f_value, fill=_MUTED)
        y += row_h + gap

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

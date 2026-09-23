"""Draw the brand band the emails and the documents share.

The logo (``templates/assets/logo_header.png``) is white, drawn for a coloured
ground, so on its own it vanishes on a white email or page. This composites it on
the questionnaire form's gradient, once, into two PNGs committed beside it:

- ``email_band.png``: the top of the email card (top corners rounded).
- ``doc_band.png``: the header of every document page (all corners rounded).

Rerun after changing the logo or the brand colours:
    python -m src.tools.make_brand_assets
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parents[2] / "templates" / "assets"

#: The form's band: linear-gradient(100deg, #00e5d0 0%, #00a8f0 45%, #2f7de1 100%).
STOPS = [(0.0, (0x00, 0xE5, 0xD0)), (0.45, (0x00, 0xA8, 0xF0)), (1.0, (0x2F, 0x7D, 0xE1))]


def _color_at(t: float) -> tuple[int, int, int]:
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))  # type: ignore[return-value]
    return STOPS[-1][1]


def band(width: int, height: int, radius: int, *, bottom_corners: bool, logo_height: int) -> Image.Image:
    grad = Image.new("RGB", (width, 1))
    for x in range(width):
        grad.putpixel((x, 0), _color_at(x / (width - 1)))
    img = grad.resize((width, height)).convert("RGBA")

    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width - 1, height - 1 + (0 if bottom_corners else radius)), radius, fill=255)
    img.putalpha(mask)

    logo = Image.open(ASSETS / "logo_header.png").convert("RGBA")
    logo = logo.resize((round(logo.width * logo_height / logo.height), logo_height), Image.LANCZOS)
    margin = (height - logo_height) // 2
    img.alpha_composite(logo, (width - logo.width - margin * 2, margin))  # RTL: the logo leads, on the right
    return img


def main() -> None:
    band(1200, 170, 28, bottom_corners=False, logo_height=96).save(ASSETS / "email_band.png", optimize=True)
    band(2000, 150, 36, bottom_corners=True, logo_height=92).save(ASSETS / "doc_band.png", optimize=True)
    for name in ("email_band.png", "doc_band.png"):
        print(name, (ASSETS / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()

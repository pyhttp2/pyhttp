"""Draw the pyhttp logo (same shapes as pyhttp/static/logo.svg) into app icons.

Writes packaging/build/icon.png (1024 px), icon.ico (Windows), icon.icns (macOS) and
icon-<size>.png for the Linux hicolor icon theme.
Needs Pillow only; no SVG renderer.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT = Path(__file__).resolve().parent / "build"
SIZE = 1024
S = SIZE / 32  # the SVG is drawn on a 32x32 grid


def _gradient(size: int) -> Image.Image:
    """Diagonal gradient #3b82f6 (top-left) -> #10b981 (bottom-right)."""
    a, b = (0x3B, 0x82, 0xF6), (0x10, 0xB9, 0x81)
    small = Image.new("RGB", (256, 256))
    px = small.load()
    for y in range(256):
        for x in range(256):
            t = (x + y) / 510
            px[x, y] = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return small.resize((size, size), Image.BICUBIC)


def _stroke(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], width: float, fill: int) -> None:
    pts = [(x * S, y * S) for x, y in points]
    draw.line(pts, fill=fill, width=round(width), joint="curve")
    r = width / 2
    for x, y in pts:  # round caps
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def draw_logo(size: int = SIZE) -> Image.Image:
    # rounded square (rect x=1 y=1 w=30 h=30 rx=8) filled with the gradient
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((1 * S, 1 * S, 31 * S, 31 * S), radius=8 * S, fill=255)
    logo = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    logo.paste(_gradient(SIZE), (0, 0), mask)

    # white arrows: request out (full white) and response back (80 % white)
    width = 2.4 * S
    top = Image.new("L", (SIZE, SIZE), 0)
    d = ImageDraw.Draw(top)
    _stroke(d, [(8, 12), (23, 12)], width, 255)
    _stroke(d, [(19, 8), (23, 12), (19, 16)], width, 255)
    bottom = Image.new("L", (SIZE, SIZE), 0)
    d = ImageDraw.Draw(bottom)
    _stroke(d, [(24, 20), (9, 20)], width, 204)
    _stroke(d, [(13, 16), (9, 20), (13, 24)], width, 204)
    arrows = ImageChops.lighter(top, bottom)
    white = Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 255))
    logo = Image.composite(white, logo, arrows)
    logo.putalpha(ImageChops.multiply(logo.getchannel("A"), mask))
    return logo if size == SIZE else logo.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logo = draw_logo()
    logo.save(OUT / "icon.png")
    for size in (16, 32, 48, 64, 128, 256, 512):
        logo.resize((size, size), Image.LANCZOS).save(OUT / f"icon-{size}.png")
    logo.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    try:
        logo.save(OUT / "icon.icns")
    except Exception as exc:  # noqa: BLE001 - PyInstaller on macOS can also convert icon.png
        print(f"note: could not write icon.icns ({exc}); the macOS build will convert icon.png instead")
    print(f"icons written to {OUT}")


if __name__ == "__main__":
    main()

"""Генерация иконки приложения (build/app_icon.ico) без внешних ассетов.

Рисуем щит с галочкой Pillow-ом в 4x-суперсэмплинге, затем даунскейлим —
получаем чистые края на всех размерах от 16 до 256 px.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    print("Нужна библиотека Pillow: pip install pillow")
    sys.exit(1)

BG_TOP = (124, 108, 255)  # акцент
BG_BOTTOM = (79, 209, 197)  # акцент2
CHECK = (255, 255, 255)

SIZES = (16, 24, 32, 48, 64, 128, 256)


def _shield_points(w: int, h: int) -> list[tuple[float, float]]:
    """Контур щита (многоугольник, сглаживается даунскейлом)."""
    pts = []
    # верхняя часть и бока
    pts.append((w * 0.5, h * 0.04))
    pts.append((w * 0.94, h * 0.2))
    for i in range(1, 13):
        t = i / 12
        x = w * (0.94 - 0.16 * t**1.6)
        y = h * (0.2 + 0.62 * t)
        pts.append((x, y))
    pts.append((w * 0.5, h * 0.97))
    for i in range(12, 0, -1):
        t = i / 12
        x = w * (0.06 + 0.16 * t**1.6)
        y = h * (0.2 + 0.62 * t)
        pts.append((x, y))
    pts.append((w * 0.06, h * 0.2))
    return pts


def render(size: int, ss: int = 4) -> Image.Image:
    w = h = size * ss
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # градиентный фон-подложка со скруглением
    radius = int(w * 0.24)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    img.putalpha(mask)

    # щит (тёмный контур внутри)
    shield = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shield)
    pad = w * 0.16
    pts = _shield_points(w - 2 * pad, h - 2 * pad)
    pts = [(x + pad, y + pad) for x, y in pts]
    sd.polygon(pts, fill=(16, 20, 45, 235))
    sd.line(pts + [pts[0]], fill=(255, 255, 255, 235), width=max(2, ss))
    img = Image.alpha_composite(img, shield)

    # галочка
    check = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cd = ImageDraw.Draw(check)
    lw = max(2, int(w * 0.075))
    cd.line(
        [
            (w * 0.34, h * 0.52),
            (w * 0.46, h * 0.64),
            (w * 0.68, h * 0.38),
        ],
        fill=CHECK + (255,),
        width=lw,
        joint="curve",
    )
    img = Image.alpha_composite(img, check)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    out = Path(__file__).resolve().parent / "app_icon.ico"
    images = [render(s) for s in SIZES]
    images[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    preview = Path(__file__).resolve().parent / "app_icon.png"
    render(256).save(preview)
    print(f"Иконка сохранена: {out} ({out.stat().st_size} байт)")
    print(f"Превью: {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

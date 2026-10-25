"""Векторные иконки, нарисованные кодом.

Никаких png/шрифтов из интернета: иконки рисуются QPainterPath, поэтому
.exe остаётся одним файлом, а линии выглядят одинаково в любом размере
и в светлой, и в тёмной теме.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

DrawFn = Callable[[QPainter, QRectF, float], None]


def _pen(color: QColor, w: float) -> QPen:
    pen = QPen(color)
    pen.setWidthF(w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


# --------------------------------------------------------------------------- #
# Отдельные иконки. Каждая рисует в квадрате r толщиной линии w.
# --------------------------------------------------------------------------- #


def d_shield(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.5, y + hh * 0.02)
    path.lineTo(x + ww * 0.96, y + hh * 0.2)
    path.cubicTo(x + ww * 0.96, y + hh * 0.62, x + ww * 0.76, y + hh * 0.88, x + ww * 0.5, y + hh * 0.98)
    path.cubicTo(x + ww * 0.24, y + hh * 0.88, x + ww * 0.04, y + hh * 0.62, x + ww * 0.04, y + hh * 0.2)
    path.closeSubpath()
    p.setPen(_pen(p.pen().color(), w))
    p.drawPath(path)


def d_shield_check(p: QPainter, r: QRectF, w: float) -> None:
    d_shield(p, r, w)
    path = QPainterPath()
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path.moveTo(x + ww * 0.32, y + hh * 0.5)
    path.lineTo(x + ww * 0.45, y + hh * 0.63)
    path.lineTo(x + ww * 0.7, y + hh * 0.36)
    p.drawPath(path)


def d_lock(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    body = QRectF(x + ww * 0.16, y + hh * 0.42, ww * 0.68, hh * 0.54)
    shackle = QPainterPath()
    shackle.moveTo(x + ww * 0.28, y + hh * 0.44)
    shackle.lineTo(x + ww * 0.28, y + hh * 0.3)
    shackle.arcTo(QRectF(x + ww * 0.28, y + hh * 0.06, ww * 0.44, hh * 0.48), 180, 180)
    shackle.lineTo(x + ww * 0.72, y + hh * 0.44)
    p.drawPath(shackle)
    p.drawRoundedRect(body, ww * 0.14, ww * 0.14)
    key = QPainterPath()
    key.moveTo(x + ww * 0.5, y + hh * 0.6)
    key.lineTo(x + ww * 0.5, y + hh * 0.76)
    p.drawPath(key)


def d_unlock(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    body = QRectF(x + ww * 0.16, y + hh * 0.42, ww * 0.68, hh * 0.54)
    shackle = QPainterPath()
    shackle.moveTo(x + ww * 0.28, y + hh * 0.44)
    shackle.lineTo(x + ww * 0.28, y + hh * 0.3)
    shackle.arcTo(QRectF(x + ww * 0.28, y + hh * 0.06, ww * 0.44, hh * 0.48), 180, 180)
    shackle.lineTo(x + ww * 0.72, y + hh * 0.22)  # дужка открыта
    p.drawPath(shackle)
    p.drawRoundedRect(body, ww * 0.14, ww * 0.14)


def d_folder(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.06, y + hh * 0.28)
    path.lineTo(x + ww * 0.06, y + hh * 0.82)
    path.cubicTo(x + ww * 0.06, y + hh * 0.9, x + ww * 0.1, y + hh * 0.92, x + ww * 0.16, y + hh * 0.92)
    path.lineTo(x + ww * 0.84, y + hh * 0.92)
    path.cubicTo(x + ww * 0.9, y + hh * 0.92, x + ww * 0.94, y + hh * 0.9, x + ww * 0.94, y + hh * 0.82)
    path.lineTo(x + ww * 0.94, y + hh * 0.36)
    path.cubicTo(x + ww * 0.94, y + hh * 0.28, x + ww * 0.9, y + hh * 0.26, x + ww * 0.84, y + hh * 0.26)
    path.lineTo(x + ww * 0.46, y + hh * 0.26)
    path.lineTo(x + ww * 0.36, y + hh * 0.14)
    path.lineTo(x + ww * 0.16, y + hh * 0.14)
    path.cubicTo(x + ww * 0.1, y + hh * 0.14, x + ww * 0.06, y + hh * 0.2, x + ww * 0.06, y + hh * 0.28)
    path.closeSubpath()
    p.drawPath(path)


def d_folder_plus(p: QPainter, r: QRectF, w: float) -> None:
    d_folder(p, r, w)
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.5, y + hh * 0.48)
    path.lineTo(x + ww * 0.5, y + hh * 0.74)
    path.moveTo(x + ww * 0.37, y + hh * 0.61)
    path.lineTo(x + ww * 0.63, y + hh * 0.61)
    p.drawPath(path)


def d_decoy(p: QPainter, r: QRectF, w: float) -> None:
    """Два слоя: «папка-двойник»."""
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    back = QPainterPath()
    back.addRoundedRect(QRectF(x + ww * 0.24, y + hh * 0.08, ww * 0.66, hh * 0.62), ww * 0.1, ww * 0.1)
    p.drawPath(back)
    front = QPainterPath()
    front.addRoundedRect(QRectF(x + ww * 0.1, y + hh * 0.3, ww * 0.66, hh * 0.62), ww * 0.1, ww * 0.1)
    p.drawPath(front)


def d_gear(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    cx, cy = x + ww / 2, y + hh / 2
    p.drawEllipse(QRectF(cx - ww * 0.17, cy - hh * 0.17, ww * 0.34, hh * 0.34))
    teeth = QPainterPath()
    for i in range(8):
        ang = math.radians(i * 45)
        r1, r2 = ww * 0.3, ww * 0.44
        teeth.moveTo(cx + r1 * math.cos(ang), cy + r1 * math.sin(ang))
        teeth.lineTo(cx + r2 * math.cos(ang), cy + r2 * math.sin(ang))
    p.drawPath(teeth)


def d_eye(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.06, y + hh * 0.5)
    path.cubicTo(x + ww * 0.28, y + hh * 0.2, x + ww * 0.72, y + hh * 0.2, x + ww * 0.94, y + hh * 0.5)
    path.cubicTo(x + ww * 0.72, y + hh * 0.8, x + ww * 0.28, y + hh * 0.8, x + ww * 0.06, y + hh * 0.5)
    p.drawPath(path)
    p.drawEllipse(QRectF(cx(r) - ww * 0.12, cy(r) - hh * 0.12, ww * 0.24, hh * 0.24))


def d_eye_off(p: QPainter, r: QRectF, w: float) -> None:
    d_eye(p, r, w)
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    slash = QPainterPath()
    slash.moveTo(x + ww * 0.14, y + hh * 0.86)
    slash.lineTo(x + ww * 0.86, y + hh * 0.14)
    p.drawPath(slash)


def d_plus(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.5, y + hh * 0.18)
    path.lineTo(x + ww * 0.5, y + hh * 0.82)
    path.moveTo(x + ww * 0.18, y + hh * 0.5)
    path.lineTo(x + ww * 0.82, y + hh * 0.5)
    p.drawPath(path)


def d_trash(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.14, y + hh * 0.26)
    path.lineTo(x + ww * 0.86, y + hh * 0.26)
    path.moveTo(x + ww * 0.38, y + hh * 0.16)
    path.lineTo(x + ww * 0.62, y + hh * 0.16)
    p.drawPath(path)
    body = QPainterPath()
    body.moveTo(x + ww * 0.24, y + hh * 0.32)
    body.lineTo(x + ww * 0.3, y + hh * 0.86)
    body.lineTo(x + ww * 0.7, y + hh * 0.86)
    body.lineTo(x + ww * 0.76, y + hh * 0.32)
    p.drawPath(body)


def d_check(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.18, y + hh * 0.54)
    path.lineTo(x + ww * 0.42, y + hh * 0.76)
    path.lineTo(x + ww * 0.84, y + hh * 0.26)
    p.drawPath(path)


def d_close(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.26, y + hh * 0.26)
    path.lineTo(x + ww * 0.74, y + hh * 0.74)
    path.moveTo(x + ww * 0.74, y + hh * 0.26)
    path.lineTo(x + ww * 0.26, y + hh * 0.74)
    p.drawPath(path)


def d_minimize(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.26, y + hh * 0.5)
    path.lineTo(x + ww * 0.74, y + hh * 0.5)
    p.drawPath(path)


def d_alert(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    tri = QPainterPath()
    tri.moveTo(x + ww * 0.5, y + hh * 0.12)
    tri.lineTo(x + ww * 0.94, y + hh * 0.86)
    tri.lineTo(x + ww * 0.06, y + hh * 0.86)
    tri.closeSubpath()
    p.drawPath(tri)
    mark = QPainterPath()
    mark.moveTo(x + ww * 0.5, y + hh * 0.42)
    mark.lineTo(x + ww * 0.5, y + hh * 0.62)
    p.drawPath(mark)
    p.drawPoint(QPointF(x + ww * 0.5, y + hh * 0.74))


def d_key(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    p.drawEllipse(QRectF(x + ww * 0.08, y + hh * 0.28, ww * 0.34, hh * 0.44))
    path = QPainterPath()
    path.moveTo(x + ww * 0.42, y + hh * 0.5)
    path.lineTo(x + ww * 0.92, y + hh * 0.5)
    path.moveTo(x + ww * 0.78, y + hh * 0.5)
    path.lineTo(x + ww * 0.78, y + hh * 0.68)
    path.moveTo(x + ww * 0.88, y + hh * 0.5)
    path.lineTo(x + ww * 0.88, y + hh * 0.64)
    p.drawPath(path)


def d_refresh(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    arc = QPainterPath()
    arc.arcMoveTo(QRectF(x + ww * 0.12, y + hh * 0.12, ww * 0.76, hh * 0.76), 60)
    arc.arcTo(QRectF(x + ww * 0.12, y + hh * 0.12, ww * 0.76, hh * 0.76), 60, 240)
    p.drawPath(arc)
    tip = QPainterPath()
    end = arc.currentPosition()
    tip.moveTo(end)
    tip.lineTo(end.x() + ww * 0.12, end.y() - hh * 0.02)
    tip.moveTo(end)
    tip.lineTo(end.x() + ww * 0.02, end.y() + hh * 0.12)
    p.drawPath(tip)


def d_clock(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    p.drawEllipse(QRectF(x + ww * 0.1, y + hh * 0.1, ww * 0.8, hh * 0.8))
    path = QPainterPath()
    path.moveTo(x + ww * 0.5, y + hh * 0.28)
    path.lineTo(x + ww * 0.5, y + hh * 0.52)
    path.lineTo(x + ww * 0.68, y + hh * 0.62)
    p.drawPath(path)


def d_external(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    box = QPainterPath()
    box.moveTo(x + ww * 0.74, y + hh * 0.16)
    box.lineTo(x + ww * 0.86, y + hh * 0.16)
    box.lineTo(x + ww * 0.86, y + hh * 0.28)
    p.drawPath(box)
    arrow = QPainterPath()
    arrow.moveTo(x + ww * 0.86, y + hh * 0.16)
    arrow.lineTo(x + ww * 0.52, y + hh * 0.5)
    p.drawPath(arrow)
    frame = QPainterPath()
    frame.moveTo(x + ww * 0.78, y + hh * 0.56)
    frame.lineTo(x + ww * 0.78, y + hh * 0.86)
    frame.lineTo(x + ww * 0.16, y + hh * 0.86)
    frame.lineTo(x + ww * 0.16, y + hh * 0.24)
    frame.lineTo(x + ww * 0.46, y + hh * 0.24)
    p.drawPath(frame)


def d_chevron_right(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.4, y + hh * 0.26)
    path.lineTo(x + ww * 0.66, y + hh * 0.5)
    path.lineTo(x + ww * 0.4, y + hh * 0.74)
    p.drawPath(path)


def d_arrow_left(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.66, y + hh * 0.26)
    path.lineTo(x + ww * 0.4, y + hh * 0.5)
    path.lineTo(x + ww * 0.66, y + hh * 0.74)
    p.drawPath(path)


def d_info(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    p.drawEllipse(QRectF(x + ww * 0.1, y + hh * 0.1, ww * 0.8, hh * 0.8))
    path = QPainterPath()
    path.moveTo(x + ww * 0.5, y + hh * 0.46)
    path.lineTo(x + ww * 0.5, y + hh * 0.72)
    p.drawPath(path)
    p.drawPoint(QPointF(x + ww * 0.5, y + hh * 0.32))


def d_search(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    p.drawEllipse(QRectF(x + ww * 0.12, y + hh * 0.12, ww * 0.54, hh * 0.54))
    path = QPainterPath()
    path.moveTo(x + ww * 0.6, y + hh * 0.6)
    path.lineTo(x + ww * 0.88, y + hh * 0.88)
    p.drawPath(path)


def d_broom(p: QPainter, r: QRectF, w: float) -> None:
    """«Зачистить следы»."""
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    handle = QPainterPath()
    handle.moveTo(x + ww * 0.82, y + hh * 0.14)
    handle.lineTo(x + ww * 0.5, y + hh * 0.46)
    p.drawPath(handle)
    body = QPainterPath()
    body.moveTo(x + ww * 0.36, y + hh * 0.36)
    body.lineTo(x + ww * 0.6, y + hh * 0.6)
    body.lineTo(x + ww * 0.42, y + hh * 0.88)
    body.lineTo(x + ww * 0.14, y + hh * 0.6)
    body.closeSubpath()
    p.drawPath(body)


def d_power(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    arc = QPainterPath()
    arc.arcMoveTo(QRectF(x + ww * 0.16, y + hh * 0.16, ww * 0.68, hh * 0.68), 300)
    arc.arcTo(QRectF(x + ww * 0.16, y + hh * 0.16, ww * 0.68, hh * 0.68), 300, 300)
    p.drawPath(arc)
    line = QPainterPath()
    line.moveTo(x + ww * 0.5, y + hh * 0.1)
    line.lineTo(x + ww * 0.5, y + hh * 0.46)
    p.drawPath(line)


def d_file(p: QPainter, r: QRectF, w: float) -> None:
    x, y, ww, hh = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath()
    path.moveTo(x + ww * 0.24, y + hh * 0.1)
    path.lineTo(x + ww * 0.58, y + hh * 0.1)
    path.lineTo(x + ww * 0.78, y + hh * 0.3)
    path.lineTo(x + ww * 0.78, y + hh * 0.9)
    path.lineTo(x + ww * 0.24, y + hh * 0.9)
    path.closeSubpath()
    p.drawPath(path)
    corner = QPainterPath()
    corner.moveTo(x + ww * 0.58, y + hh * 0.1)
    corner.lineTo(x + ww * 0.58, y + hh * 0.3)
    corner.lineTo(x + ww * 0.78, y + hh * 0.3)
    p.drawPath(corner)


ICONS: dict[str, DrawFn] = {
    "shield": d_shield,
    "shield-check": d_shield_check,
    "lock": d_lock,
    "unlock": d_unlock,
    "folder": d_folder,
    "folder-plus": d_folder_plus,
    "decoy": d_decoy,
    "gear": d_gear,
    "eye": d_eye,
    "eye-off": d_eye_off,
    "plus": d_plus,
    "trash": d_trash,
    "check": d_check,
    "close": d_close,
    "minimize": d_minimize,
    "alert": d_alert,
    "key": d_key,
    "refresh": d_refresh,
    "clock": d_clock,
    "external": d_external,
    "chevron-right": d_chevron_right,
    "arrow-left": d_arrow_left,
    "info": d_info,
    "search": d_search,
    "broom": d_broom,
    "power": d_power,
    "file": d_file,
}


def cx(r: QRectF) -> float:
    return r.x() + r.width() / 2


def cy(r: QRectF) -> float:
    return r.y() + r.height() / 2


_cache: dict[tuple, QPixmap] = {}


def pixmap(name: str, color: str, size: int = 24, stroke: float = 1.9, scale: float = 2.0) -> QPixmap:
    """Кэш-фицированный QPixmap иконки нужного цвета и размера."""
    key = (name, color, size, round(stroke, 2), scale)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    draw = ICONS.get(name)
    px = QPixmap(int(size * scale), int(size * scale))
    px.fill(Qt.GlobalColor.transparent)
    if draw is not None:
        painter = QPainter(px)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.scale(scale, scale)
        pen = _pen(QColor(color), stroke)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        margin = max(stroke, 1.2)
        rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
        painter.setPen(pen)
        draw(painter, rect, stroke)
        painter.end()
    # помечаем пиксмап как HiDPI: тогда логический размер равен `size`,
    # а физически рисуется scale-кратное число пикселей (чёткость на Retina/200%)
    px.setDevicePixelRatio(scale)
    _cache[key] = px
    return px


def icon(name: str, color: str, size: int = 24, stroke: float = 1.9) -> QIcon:
    return QIcon(pixmap(name, color, size, stroke))


def tinted(name: str, color: str, size: int = 24, stroke: float = 1.9) -> QIcon:
    """То же, что :func:`icon`, — имя оставлено для читаемости в местах, где цвет меняется."""
    return icon(name, color, size, stroke)

"""Мобильные виджеты: карточки, переключатели, нижняя навигация, тосты, «простыни».

Всё рисуется штатным Qt без сторонних библиотек, поэтому интерфейс одинаково
выглядит и в собранном .exe, и при запуске из исходников.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Property,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .icons import icon, pixmap
from .theme import NIGHT, Palette, RADIUS_CARD


# --------------------------------------------------------------------------- #
# Базовые помощники
# --------------------------------------------------------------------------- #


def add_shadow(widget: QWidget, color: str, blur: int = 28, offset: int = 6, alpha: float = 0.45) -> None:
    """Мягкая тень. Если графическая система не поддерживает эффекты — молча пропускаем."""
    try:
        effect = QGraphicsDropShadowEffect(widget)
        c = QColor(color)
        c.setAlphaF(alpha)
        effect.setColor(c)
        effect.setBlurRadius(blur)
        effect.setOffset(0, offset)
        widget.setGraphicsEffect(effect)
    except Exception:  # pragma: no cover - зависит от платформы
        pass


class ElidedLabel(QLabel):
    """Надпись, которая не растягивает карточку, а обрезается многоточием.

    Обычный QLabel с длинным путём заставляет весь список быть шире окна;
    здесь минимальная ширина равна нулю, а текст усекается по месту.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        # политику НЕ меняем: Ignored заставил бы layout дать лейблу нулевую
        # ширину рядом со stretch-элементом. Достаточно обнулить минимум.

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        return hint.__class__(0, hint.height())

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        return hint.__class__(min(hint.width(), 240), hint.height())

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QPainter, QPalette

        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        elided = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, self.width()
        )
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
        painter.end()


class Card(QFrame):
    """Скруглённая карточка-контейнер."""

    def __init__(self, parent: QWidget | None = None, hoverable: bool = False, flat: bool = False):
        super().__init__(parent)
        self.setObjectName("CardFlat" if flat else "Card")
        if hoverable:
            self.setProperty("hoverable", True)
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(18, 16, 18, 16)
        self._layout.setSpacing(10)

    @property
    def layout_(self) -> QVBoxLayout:
        return self._layout

    def add(self, *widgets: QWidget) -> "Card":
        for w in widgets:
            self._layout.addWidget(w)
        return self


class SectionLabel(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text.upper(), parent)
        self.setObjectName("SectionTitle")


class TitleLabel(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("PageTitle")


class Divider(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFixedHeight(1)


# --------------------------------------------------------------------------- #
# Кнопки (рисуются вручную: вид не зависит от особенностей QSS на разных ОС)
# --------------------------------------------------------------------------- #


class StyledButton(QPushButton):
    """Кнопка с собственной отрисовкой фона, градиента и наведений."""

    KINDS = ("primary", "danger", "ghost", "plain", "icon", "winbtn")

    def __init__(
        self,
        text: str = "",
        palette: Palette | None = None,
        kind: str = "plain",
        icon_name: str | None = None,
        icon_color: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(text, parent)
        self._palette = palette if palette is not None else NIGHT
        self._kind = kind if kind in self.KINDS else "plain"
        self._icon_name = icon_name
        self._icon_color = icon_color
        self._icon_px = 0
        self._hover = False
        self._press = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # QSS не красит фон кнопок — отключаем его влияние совсем
        self.setStyleSheet("background: transparent; border: none; padding: 0px;")
        if kind == "icon":
            self.setFixedSize(36, 36)
        elif kind == "winbtn":
            self.setFixedSize(30, 30)
        elif kind == "primary":
            self.setMinimumHeight(54)
        else:
            self.setMinimumHeight(48)
        self._update_icon()

    # -- публичное API ----------------------------------------------------- #
    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._update_icon()
        self.update()

    def set_kind(self, kind: str) -> None:
        self._kind = kind if kind in self.KINDS else "plain"
        self._update_icon()
        self.update()

    def set_icon(self, icon_name: str | None, color: str | None = None, px: int = 0) -> None:
        self._icon_name = icon_name
        if color is not None:
            self._icon_color = color
        if px:
            self._icon_px = px
        self._update_icon()
        self.update()

    # -- внутреннее -------------------------------------------------------- #
    def _icon_size(self) -> int:
        if self._icon_px:
            return self._icon_px
        return {"primary": 22, "danger": 20, "ghost": 20, "icon": 18, "winbtn": 14, "plain": 20}[self._kind]

    def _fg(self) -> QColor:
        p = self._palette
        if not self.isEnabled():
            return QColor(p.muted)
        if self._kind == "primary":
            return QColor("#FFFFFF")
        if self._kind == "danger":
            return QColor("#FFFFFF") if self._hover else QColor(p.danger)
        if self._kind == "ghost":
            return QColor(p.text) if self._hover else QColor(p.muted)
        if self._kind in ("icon", "winbtn"):
            base = self._icon_color or p.muted
            if self.objectName() == "CloseButton" and self._hover:
                return QColor("#FFFFFF")
            return QColor(base)
        return QColor(p.text)

    def _radius(self) -> int:
        return {"primary": 18, "danger": 18, "ghost": 18, "plain": 16, "icon": 12, "winbtn": 10}[self._kind]

    def _update_icon(self) -> None:
        if self._icon_name:
            color = self._icon_color or self._fg().name()
            if self._kind == "danger" and not self._icon_color:
                color = self._palette.danger
            self._icon_pixmap = pixmap(self._icon_name, color, self._icon_size())
        else:
            self._icon_pixmap = None

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._update_icon()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._press = False
        self._update_icon()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._press = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = self._palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        radius = self._radius()
        enabled = self.isEnabled()

        if self._kind == "primary":
            if enabled:
                a, b = (p.accent2, p.accent) if self._hover else (p.accent, p.accent2)
                grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
                grad.setColorAt(0.0, QColor(a))
                grad.setColorAt(1.0, QColor(b))
                if self._press:
                    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
                    grad.setColorAt(0.0, QColor(p.accent))
                    grad.setColorAt(1.0, QColor(p.accent))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(grad))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(p.surface)))
            painter.drawRoundedRect(rect, radius, radius)
        elif self._kind == "danger":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(p.danger) if self._hover and enabled else QColor(p.danger_soft)))
            painter.drawRoundedRect(rect, radius, radius)
        elif self._kind == "ghost":
            painter.setPen(QPen(QColor(p.accent if self._hover and enabled else p.border), 1))
            painter.setBrush(QBrush(QColor(p.surface_alt) if self._hover and enabled else QColor(0, 0, 0, 0)))
            painter.drawRoundedRect(rect, radius, radius)
        elif self._kind in ("icon", "winbtn"):
            if self._hover and enabled:
                color = QColor(p.danger) if self.objectName() == "CloseButton" else QColor(p.surface_hover)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(color))
                painter.drawRoundedRect(rect, radius, radius)
        else:  # plain
            if self._press and enabled:
                bg = QColor(p.accent_soft)
            elif self._hover and enabled:
                bg = QColor(p.surface_hover)
            elif enabled:
                bg = QColor(p.surface_alt)
            else:
                bg = QColor(p.surface)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(bg))
            painter.drawRoundedRect(rect, radius, radius)

        # иконка + текст по центру
        fg = self._fg()
        font = self.font()
        font.setBold(self._kind == "primary")
        painter.setFont(font)
        painter.setPen(QPen(fg, 1))
        text = self.text()
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(text) if text else 0
        icon_w = self._icon_pixmap.width() // max(1, self._icon_pixmap.devicePixelRatio()) if self._icon_pixmap else 0
        spacing = 10 if (text and icon_w) else 0
        total = text_w + icon_w + spacing
        x = (self.width() - total) // 2
        if self._icon_pixmap is not None:
            iy = (self.height() - icon_w) // 2
            painter.drawPixmap(int(x), int(iy), self._icon_pixmap)
            x += icon_w + spacing
        if text:
            painter.drawText(
                QRect(int(x), 0, int(text_w), self.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
        painter.end()


class PrimaryButton(StyledButton):
    def __init__(self, text: str, icon_name: str | None = None, palette: Palette | None = None, parent=None):
        super().__init__(text, palette, "primary", icon_name, "#FFFFFF", parent)


class GhostButton(StyledButton):
    def __init__(self, text: str, icon_name: str | None = None, palette: Palette | None = None, parent=None):
        super().__init__(text, palette, "ghost", icon_name, None, parent)


class DangerButton(StyledButton):
    def __init__(self, text: str, icon_name: str | None = None, palette: Palette | None = None, parent=None):
        super().__init__(text, palette, "danger", icon_name, None, parent)


class IconButton(StyledButton):
    """Круглая кнопка-иконка (действие в карточке)."""

    def __init__(self, icon_name: str, color: str, tooltip: str = "", size: int = 18, parent=None):
        super().__init__("", None, "icon", icon_name, color, parent)
        self._icon_px = size
        self.setFixedSize(size + 20, size + 20)
        self._update_icon()
        if tooltip:
            self.setToolTip(tooltip)


class WindowButton(StyledButton):
    def __init__(self, icon_name: str, color: str, name: str = "", parent=None):
        super().__init__("", None, "winbtn", icon_name, color, parent)
        if name:
            self.setObjectName(name)
        self._icon_px = 14
        self._update_icon()


# --------------------------------------------------------------------------- #
# Переключатель (свитч)
# --------------------------------------------------------------------------- #


class Switch(QWidget):
    """Мобильный свитч с плавной анимацией."""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(52, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = bool(checked)
        self._pos = 1.0 if self._checked else 0.0
        self._palette: Palette | None = None

        self._anim = QPropertyAnimation(self, b"knob")
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def isChecked(self) -> bool:  # noqa: N802 - совпадаем с API Qt
        return self._checked

    def setChecked(self, value: bool, animate: bool = True) -> None:  # noqa: N802
        value = bool(value)
        if value == self._checked:
            return
        self._checked = value
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(1.0 if value else 0.0)
            self._anim.start()
        else:
            self._pos = 1.0 if value else 0.0
        self.update()
        self.toggled.emit(value)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, value: float) -> None:
        self._pos = float(value)
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = self._palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        radius = h / 2

        off = QColor(p.field_bg if p else "#141B3A")
        on = QColor(p.accent if p else "#7C6CFF")
        border = QColor(p.border if p else "#2C366B")
        track = QColor(off)
        track.setAlphaF(1.0)
        mixed = _mix(off, on, self._pos)
        painter.setPen(QPen(border if self._pos < 0.5 else on, 1))
        painter.setBrush(QBrush(mixed))
        painter.drawRoundedRect(QRect(1, 1, w - 2, h - 2), radius, radius)

        knob_r = h / 2 - 5
        travel = w - 2 * knob_r - 8
        kx = 4 + knob_r + travel * self._pos
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawEllipse(QRect(int(kx - knob_r), int(h / 2 - knob_r), int(knob_r * 2), int(knob_r * 2)))
        painter.end()


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


class ToggleRow(QFrame):
    """Строка настройки: заголовок, описание, свитч."""

    toggled = Signal(bool)

    def __init__(self, title: str, description: str, checked: bool, palette: Palette, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.palette_ = palette
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(14)

        texts = QVBoxLayout()
        texts.setSpacing(3)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        t.setWordWrap(True)
        d = QLabel(description)
        d.setObjectName("CardText")
        d.setWordWrap(True)
        texts.addWidget(t)
        texts.addWidget(d)

        self.switch = Switch(checked)
        self.switch.set_palette(palette)
        self.switch.toggled.connect(self.toggled.emit)

        lay.addLayout(texts, 1)
        lay.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)

    def setChecked(self, value: bool) -> None:  # noqa: N802
        self.switch.setChecked(value, animate=False)

    def isChecked(self) -> bool:  # noqa: N802
        return self.switch.isChecked()


# --------------------------------------------------------------------------- #
# Статус-чип
# --------------------------------------------------------------------------- #


class StatusChip(QLabel):
    KINDS = ("locked", "unlocked", "neutral", "warn")

    def __init__(self, text: str, kind: str = "neutral", parent=None):
        super().__init__(text, parent)
        self.set_kind(kind)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_kind(self, kind: str) -> None:
        if kind not in self.KINDS:
            kind = "neutral"
        self.setProperty("chip", kind)
        self.style().unpolish(self)
        self.style().polish(self)


# --------------------------------------------------------------------------- #
# Поле пароля
# --------------------------------------------------------------------------- #


class PasswordField(QLineEdit):
    """Поле пароля с кнопкой «показать» и без подсветки ошибок (чтобы не выдавать)."""

    def __init__(self, placeholder: str = "Пароль", palette: Palette | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("PasswordField")
        self.setPlaceholderText(placeholder)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self.setMinimumHeight(58)
        self._palette = palette
        self._visible = False

        self.toggle_btn = QPushButton(self)
        self.toggle_btn.setProperty("style", "icon")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setFixedSize(40, 40)
        self.toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle_btn.clicked.connect(self.toggle_visibility)
        self._refresh_icon()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 8, 6)
        lay.addStretch(1)
        lay.addWidget(self.toggle_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setTextMargins(4, 4, 46, 4)

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        color = (self._palette.muted if self._palette else "#8E97C4")
        self.toggle_btn.setIcon(icon("eye-off" if self._visible else "eye", color, 20))

    def toggle_visibility(self) -> None:
        self._visible = not self._visible
        self.setEchoMode(QLineEdit.EchoMode.Normal if self._visible else QLineEdit.EchoMode.Password)
        self._refresh_icon()
        self.setFocus()


# --------------------------------------------------------------------------- #
# Нижняя навигация
# --------------------------------------------------------------------------- #


class NavButton(QPushButton):
    """Пункт нижней навигации: иконка над подписью, активный — с пилюлей."""

    def __init__(self, text: str, icon_name: str, palette: Palette, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background: transparent; border: none; padding: 0px;")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._text = text
        self._icon_name = icon_name
        self._palette = palette
        self._active = False
        self._hover = False
        self.setMinimumHeight(58)
        self._update_icon()

    def _update_icon(self) -> None:
        color = self._palette.accent if self._active else self._palette.muted
        self._icon_pixmap = pixmap(self._icon_name, color, 22)

    def set_active(self, active: bool) -> None:
        if self._active == active:
            return
        self._active = active
        self._update_icon()
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = self._palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(4, 6, -4, 2)
        if self._active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(p.accent_soft)))
            painter.drawRoundedRect(rect, 16, 16)
        elif self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(p.surface_alt)))
            painter.drawRoundedRect(rect, 16, 16)

        iw = self._icon_pixmap.width() // max(1, self._icon_pixmap.devicePixelRatio())
        ix = (self.width() - iw) // 2
        painter.drawPixmap(int(ix), int(rect.y() + 8), self._icon_pixmap)

        color = QColor(p.accent) if self._active else QColor(p.muted)
        font = self.font()
        font.setPointSizeF(8.5)
        font.setBold(self._active)
        painter.setFont(font)
        painter.setPen(QPen(color, 1))
        painter.drawText(
            QRect(0, int(rect.y() + iw + 10), self.width(), 16),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self._text,
        )
        painter.end()


class NavBar(QFrame):
    """Нижняя навигация с бегающим индикатором."""

    tab_selected = Signal(int)

    def __init__(self, tabs: list[tuple[str, str]], palette: Palette, parent=None):
        super().__init__(parent)
        self.setObjectName("NavBar")
        self.setFixedHeight(74)
        self._palette = palette
        self._buttons: list[NavButton] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(4)

        self.indicator = QFrame(self)
        self.indicator.setObjectName("NavIndicator")
        self.indicator.setFixedHeight(6)
        self.indicator.setFixedWidth(28)

        row = QHBoxLayout()
        row.setSpacing(4)
        for text, icon_name in tabs:
            btn = NavButton(text, icon_name, palette)
            btn.clicked.connect(lambda _=False, b=btn: self._on_click(b))
            row.addWidget(btn)
            self._buttons.append(btn)
        outer.addLayout(row)
        outer.addWidget(self.indicator, 0, Qt.AlignmentFlag.AlignHCenter)

        self._current = -1

    def _on_click(self, btn: NavButton) -> None:
        self.select(self._buttons.index(btn))

    def select(self, index: int, emit: bool = True) -> None:
        if index == self._current:
            if emit:
                self.tab_selected.emit(index)
            return
        self._current = index
        for i, b in enumerate(self._buttons):
            b.set_active(i == index)
        QTimer.singleShot(0, self._move_indicator)
        if emit:
            self.tab_selected.emit(index)

    def _move_indicator(self) -> None:
        if self._current < 0 or self._current >= len(self._buttons):
            return
        btn = self._buttons[self._current]
        center = btn.mapTo(self, QPoint(btn.width() // 2, 0)).x()
        target = center - self.indicator.width() // 2
        start = self.indicator.x()
        # родитель обязателен: без него питоновская ссылка умирает и анимация обрывается
        anim = QPropertyAnimation(self.indicator, b"pos", self)
        anim.setDuration(220)
        anim.setStartValue(QPoint(start, self.indicator.y()))
        anim.setEndValue(QPoint(target, self.indicator.y()))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._move_indicator()


# --------------------------------------------------------------------------- #
# Тост
# --------------------------------------------------------------------------- #


class Toast(QFrame):
    """Всплывающее сообщение снизу."""

    def __init__(self, parent: QWidget, palette: Palette):
        super().__init__(parent)
        self.setObjectName("Toast")
        self._palette = palette
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)
        self._icon = QLabel(self)
        self._label = QLabel("", self)
        self._label.setObjectName("ToastText")
        self._label.setWordWrap(True)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._label, 1)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._hide_animated)
        add_shadow(self, palette.shadow, blur=24, offset=4, alpha=0.5)

    def show_message(self, text: str, kind: str = "info", ms: int = 3200) -> None:
        colors = {
            "info": self._palette.text,
            "ok": self._palette.success,
            "warn": self._palette.warning,
            "error": self._palette.danger,
        }
        names = {"info": "info", "ok": "check", "warn": "alert", "error": "alert"}
        color = colors.get(kind, self._palette.text)
        self._icon.setPixmap(pixmap(names.get(kind, "info"), color, 18))
        self._icon.setFixedSize(18, 18)
        self._label.setText(text)
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(180)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._timer.start(ms)

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        w = min(self.sizeHint().width() + 8, parent.width() - 32)
        self.setFixedWidth(max(w, 200))
        h = max(self.sizeHint().height(), 46)
        self.setFixedHeight(h)
        self.move((parent.width() - self.width()) // 2, parent.height() - h - 96)

    def _hide_animated(self) -> None:
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(200)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self.hide)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


# --------------------------------------------------------------------------- #
# Анимированный стек страниц
# --------------------------------------------------------------------------- #


class AnimatedStack(QStackedWidget):
    """Стек страниц с горизонтальным сдвигом, как в мобильных приложениях."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animating = False

    def go_to(self, index: int, direction: int = 0) -> None:
        if index == self.currentIndex() or self._animating:
            self.setCurrentIndex(index)
            return
        current = self.currentWidget()
        nxt = self.widget(index)
        if current is None or nxt is None:
            self.setCurrentIndex(index)
            return

        width = self.width() or 420
        if direction == 0:
            direction = 1 if index > self.currentIndex() else -1

        nxt.move(width * direction, 0)
        nxt.show()
        nxt.raise_()
        self.setCurrentIndex(index)

        a1 = QPropertyAnimation(nxt, b"pos")
        a1.setDuration(240)
        a1.setEasingCurve(QEasingCurve.Type.OutCubic)
        a1.setStartValue(QPoint(width * direction, 0))
        a1.setEndValue(QPoint(0, 0))
        a2 = QPropertyAnimation(current, b"pos")
        a2.setDuration(240)
        a2.setEasingCurve(QEasingCurve.Type.OutCubic)
        a2.setStartValue(QPoint(0, 0))
        a2.setEndValue(QPoint(-width * direction // 3, 0))

        group = QParallelAnimationGroup(self)
        group.addAnimation(a1)
        group.addAnimation(a2)
        self._animating = True
        group.finished.connect(lambda: self._done(current))
        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

    def _done(self, previous: QWidget) -> None:
        previous.hide()
        previous.move(0, 0)
        self._animating = False

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if w := self.currentWidget():
            w.move(0, 0)


# --------------------------------------------------------------------------- #
# «Простыня» (bottom sheet) — модальное окно поверх контента
# --------------------------------------------------------------------------- #


class Sheet(QFrame):
    """Нижняя «простыня»: затемнение + панель с контентом."""

    closed = Signal()

    def __init__(self, parent: QWidget, palette: Palette, title: str):
        super().__init__(parent)
        self.setObjectName("SheetDim")
        self._palette = palette
        self._parent = parent
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

        self.panel = QFrame(self)
        self.panel.setObjectName("Sheet")
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        add_shadow(self.panel, palette.shadow, blur=30, offset=-4, alpha=0.5)

        self._panel_layout = QVBoxLayout(self.panel)
        self._panel_layout.setContentsMargins(20, 10, 20, 22)
        self._panel_layout.setSpacing(12)

        handle = QFrame(self.panel)
        handle.setObjectName("SheetHandle")
        handle.setFixedWidth(44)
        self._panel_layout.addWidget(handle, 0, Qt.AlignmentFlag.AlignHCenter)

        head = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        f = QFont(self.title_label.font())
        f.setPointSize(13)
        f.setBold(True)
        self.title_label.setFont(f)
        close_btn = IconButton("close", palette.muted, "Закрыть", 18)
        close_btn.clicked.connect(self.close_sheet)
        head.addWidget(self.title_label)
        head.addStretch(1)
        head.addWidget(close_btn)
        self._panel_layout.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self._panel_layout.addLayout(self.body)

    def add_widget(self, widget: QWidget) -> None:
        self.body.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)

    def open_sheet(self) -> None:
        self.setGeometry(self._parent.rect())
        self.show()
        self.raise_()
        self._relayout()
        start = self.panel.y()
        anim = QPropertyAnimation(self.panel, b"pos", self)
        anim.setDuration(260)
        anim.setStartValue(QPoint(self.panel.x(), self._parent.height()))
        anim.setEndValue(QPoint(self.panel.x(), start))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _relayout(self) -> None:
        self.panel.adjustSize()
        h = min(self.panel.sizeHint().height() + 10, int(self._parent.height() * 0.86))
        self.panel.setGeometry(0, self._parent.height() - h, self._parent.width(), h)

    def close_sheet(self) -> None:
        anim = QPropertyAnimation(self.panel, b"pos", self)
        anim.setDuration(200)
        anim.setStartValue(self.panel.pos())
        anim.setEndValue(QPoint(self.panel.x(), self._parent.height()))
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self.hide)
        anim.finished.connect(self.closed.emit)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.isVisible():
            self._relayout()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self.panel.geometry().contains(event.position().toPoint()):
            self.close_sheet()
        super().mousePressEvent(event)


class ProgressSheet(Sheet):
    """Простыня с прогрессом долгой операции."""

    cancelled = Signal()

    def __init__(self, parent: QWidget, palette: Palette, title: str = "Работаем…"):
        super().__init__(parent, palette, title)
        self.status = QLabel("Подготовка…")
        self.status.setObjectName("CardText")
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        self.counter = QLabel("0 / 0")
        self.counter.setObjectName("CardText")
        self.add_widget(self.status)
        self.add_widget(self.bar)
        self.add_widget(self.counter)
        self._cancel_btn: QPushButton | None = None

    def enable_cancel(self, text: str = "Прервать") -> None:
        if self._cancel_btn is None:
            self._cancel_btn = GhostButton(text, None, self._palette)
            self._cancel_btn.clicked.connect(self.cancelled.emit)
            self.add_widget(self._cancel_btn)

    def set_progress(self, done: int, total: int, message: str = "") -> None:
        total = max(total, 1)
        self.bar.setValue(int(done * 100 / total))
        self.counter.setText(f"{done} / {total}")
        if message:
            short = message if len(message) < 64 else "…" + message[-63:]
            self.status.setText(short)

    def set_busy(self, message: str = "") -> None:
        self.bar.setRange(0, 0)
        if message:
            self.status.setText(message)

    def set_finished(self) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(100)


# --------------------------------------------------------------------------- #
# Карточка папки
# --------------------------------------------------------------------------- #


class FolderCard(Card):
    """Карточка защищённой папки в списке."""

    clicked = Signal()
    action = Signal(str)  # "open" | "toggle" | "remove"

    def __init__(self, palette: Palette, parent=None):
        super().__init__(parent, hoverable=True)
        self._palette = palette
        self.setMinimumHeight(96)

        row = QHBoxLayout()
        row.setSpacing(14)

        self.badge = QLabel()
        self.badge.setFixedSize(46, 46)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setStyleSheet(
            f"background: {palette.accent_soft}; border-radius: 16px;"
        )

        texts = QVBoxLayout()
        texts.setSpacing(4)
        self.name = ElidedLabel("Папка")
        self.name.setObjectName("CardTitle")
        self.path = ElidedLabel("")
        self.path.setObjectName("CardText")
        self.meta = ElidedLabel("")
        self.meta.setObjectName("CardText")
        texts.addWidget(self.name)
        texts.addWidget(self.path)
        texts.addWidget(self.meta)

        # правая колонка: чип и кнопки прижаты вправо через растяжки,
        # потому что QBoxLayout.addLayout() не принимает alignment
        right = QVBoxLayout()
        right.setSpacing(8)
        self.chip = StatusChip("Защищено", "locked")
        chip_row = QHBoxLayout()
        chip_row.addStretch(1)
        chip_row.addWidget(self.chip)

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(6)
        self.buttons.addStretch(1)

        right.addLayout(chip_row)
        right.addLayout(self.buttons)

        # фиксированная ширина правой колонки: текст карточки её не вытесняет
        self.right_widget = QWidget()
        self.right_widget.setFixedWidth(130)
        self.right_widget.setLayout(right)

        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(texts, 1)
        row.addWidget(self.right_widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self.layout_.addLayout(row)

        self.installEventFilter(self)

    def set_badge(self, icon_name: str, color: str | None = None) -> None:
        self.badge.setPixmap(pixmap(icon_name, color or self._palette.accent, 24))

    def set_actions(self, actions: list[tuple[str, str, str]]) -> None:
        """actions: [(ключ, иконка, подсказка)]"""
        while self.buttons.count():
            item = self.buttons.takeAt(0)
            if w := item.widget():
                w.deleteLater()
        for key, icon_name, tip in actions:
            btn = IconButton(icon_name, self._palette.muted, tip, 18)
            btn.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            self.buttons.addWidget(btn)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self and event.type() == QEvent.Type.MouseButtonRelease:
            self.clicked.emit()
        return super().eventFilter(obj, event)


# --------------------------------------------------------------------------- #
# Пустое состояние
# --------------------------------------------------------------------------- #


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str, palette: Palette, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 40, 24, 24)
        lay.setSpacing(12)
        badge = QLabel()
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(pixmap(icon_name, palette.muted, 44, 1.6))
        t = QLabel(title)
        t.setObjectName("CardTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d = QLabel(text)
        d.setObjectName("CardText")
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d.setWordWrap(True)
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(t)
        lay.addWidget(d)
        lay.addStretch(1)


class StatTile(QFrame):
    """Плитка статистики (число + подпись)."""

    def __init__(self, value: str, label: str, palette: Palette, parent=None):
        super().__init__(parent)
        self.setObjectName("CardFlat")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(2)
        self.value = QLabel(value)
        self.value.setObjectName("BigNumber")
        self.label = QLabel(label)
        self.label.setObjectName("CardText")
        lay.addWidget(self.value)
        lay.addWidget(self.label)

    def set_value(self, value: str) -> None:
        self.value.setText(value)


def stretch_row(*widgets: QWidget, spacing: int = 10) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    return lay


def set_expand(widget: QWidget) -> None:
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)


def noop_action() -> Callable[[], None]:  # pragma: no cover - служебное
    return lambda: None


__all__ = [
    "AnimatedStack",
    "Card",
    "DangerButton",
    "Divider",
    "EmptyState",
    "FolderCard",
    "GhostButton",
    "IconButton",
    "NavBar",
    "PasswordField",
    "PrimaryButton",
    "ProgressSheet",
    "SectionLabel",
    "Sheet",
    "StatTile",
    "StatusChip",
    "Switch",
    "TitleLabel",
    "Toast",
    "ToggleRow",
    "WindowButton",
    "add_shadow",
]

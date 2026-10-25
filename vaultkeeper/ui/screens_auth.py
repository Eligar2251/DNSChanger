"""Экраны авторизации: блокировка, первичная настройка, аварийный возврат.

Важная деталь дизайна: экран ввода пароля НИЧЕМ не выдаёт, что неверный
пароль откроет обманки. Никаких подсказок, ошибок и красных рамок —
интерфейс всегда отвечает «Доступ открыт».
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from ..core.fsutil import display_path
from .icons import pixmap
from .theme import Palette
from .widgets import (
    Card,
    GhostButton,
    PasswordField,
    PrimaryButton,
    SectionLabel,
    add_shadow as _shadow,
)


def _center(label: QLabel) -> QLabel:
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


class ScrollPage(QScrollArea):
    """База страницы: вертикальный скролл без рамок."""

    def __init__(self, palette: Palette, parent=None):
        super().__init__(parent)
        self.palette_ = palette
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background: transparent;")
        self.content = QWidget()
        self.content.setObjectName("PageHost")
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(20, 8, 20, 24)
        self.layout.setSpacing(14)
        self.setWidget(self.content)

    def add(self, *widgets: QWidget) -> None:
        for w in widgets:
            self.layout.addWidget(w)

    def add_stretch(self, size: int = 1) -> None:
        self.layout.addStretch(size)

    def refresh(self) -> None:
        """Перерисовать данные (переопределяется в страницах)."""


# --------------------------------------------------------------------------- #
# Экран блокировки
# --------------------------------------------------------------------------- #


class LockPage(ScrollPage):
    submit = Signal(str)
    recover_requested = Signal()
    code_requested = Signal()

    def __init__(self, palette: Palette, parent=None):
        super().__init__(palette, parent)
        self.layout.setSpacing(12)

        self.clock = QLabel("--:--")
        self.clock.setObjectName("LockClock")
        self.date = QLabel("")
        self.date.setObjectName("LockDate")
        _center(self.clock)
        _center(self.date)

        self.badge = QLabel()
        self.badge.setFixedSize(92, 92)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setPixmap(pixmap("shield-check", palette.accent, 46, 1.7))
        self.badge.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {palette.accent_soft},"
            f" stop:1 {palette.surface_alt}); border-radius: 30px;"
        )
        _shadow(self.badge, palette.shadow, blur=30, offset=8, alpha=0.5)

        self.title = QLabel("Ваши папки под защитой")
        self.title.setObjectName("LockStatus")
        _center(self.title)

        self.subtitle = QLabel("Введите пароль, чтобы вернуть файлы в папки")
        self.subtitle.setObjectName("LockHint")
        self.subtitle.setWordWrap(True)
        _center(self.subtitle)

        self.password = PasswordField("Пароль", palette)
        self.password.returnPressed.connect(self._on_submit)
        self.button = PrimaryButton("Открыть", "unlock", palette)
        self.button.setMinimumHeight(58)
        self.button.clicked.connect(self._on_submit)

        links = QHBoxLayout()
        links.setSpacing(10)
        self.recover_btn = GhostButton("Аварийный доступ", "key", palette)
        self.code_btn = GhostButton("Код восстановления", None, palette)
        self.recover_btn.clicked.connect(self.recover_requested.emit)
        self.code_btn.clicked.connect(self.code_requested.emit)
        links.addWidget(self.recover_btn)
        links.addWidget(self.code_btn)

        self.status_card = Card(flat=True)
        self.status_text = QLabel("")
        self.status_text.setObjectName("CardText")
        self.status_text.setWordWrap(True)
        self.status_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_card.add(self.status_text)
        self.status_card.hide()

        self.add_stretch(1)
        self.add(self.clock, self.date)
        self.layout.addSpacing(18)
        self.layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignHCenter)
        self.layout.addSpacing(14)
        self.add(self.title, self.subtitle)
        self.layout.addSpacing(18)
        self.add(self.password)
        self.add(self.button)
        self.layout.addSpacing(6)
        self.layout.addLayout(links)
        self.add(self.status_card)
        self.add_stretch(2)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_clock)
        self._update_clock()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._timer.start()
        self.password.clear()
        self.password.setFocus()
        self.status_card.hide()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def _update_clock(self) -> None:
        import datetime

        now = datetime.datetime.now()
        self.clock.setText(now.strftime("%H:%M"))
        weekdays = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        months = [
            "января", "февраля", "марта", "апреля", "мая", "июня",
            "июля", "августа", "сентября", "октября", "ноября", "декабря",
        ]
        self.date.setText(f"{weekdays[now.weekday()]}, {now.day} {months[now.month - 1]}")

    def _on_submit(self) -> None:
        password = self.password.text()
        if not password:
            self.password.setFocus()
            return
        self.password.clear()
        self.submit.emit(password)

    def set_info(self, text: str) -> None:
        """Нейтральная информация на экране (например, число папок под защитой)."""
        if text:
            self.status_text.setText(text)
            self.status_card.show()
        else:
            self.status_card.hide()

    def set_emergency_visible(self, visible: bool) -> None:
        self.recover_btn.setVisible(visible)


# --------------------------------------------------------------------------- #
# Экран аварийного возврата
# --------------------------------------------------------------------------- #


class CrashPage(ScrollPage):
    restore = Signal()
    keep_locked = Signal()

    def __init__(self, palette: Palette, parent=None):
        super().__init__(palette, parent)
        badge = QLabel()
        badge.setFixedSize(84, 84)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(pixmap("alert", palette.warning, 42, 1.7))
        badge.setStyleSheet(f"background: {palette.surface_alt}; border-radius: 28px;")

        title = QLabel("Приложение было закрыто аварийно")
        title.setObjectName("PageTitle")
        title.setWordWrap(True)

        text = QLabel(
            "Процесс завершился нештатно (например, через Диспетчер задач).\n"
            "Ваши файлы целы — они в зашифрованном хранилище.\n\n"
            "Можно вернуть всё как было: файлы снова появятся в своих папках."
        )
        text.setObjectName("CardText")
        text.setWordWrap(True)

        self.restore_btn = PrimaryButton("Вернуть всё как было", "refresh", palette)
        self.restore_btn.clicked.connect(self.restore.emit)
        self.keep_btn = GhostButton("Оставить заблокированным", "lock", palette)
        self.keep_btn.clicked.connect(self.keep_locked.emit)

        note = Card(flat=True)
        note_text = QLabel(
            "Аварийный ключ привязан к вашей учётной записи Windows, "
            "поэтому пароль не потребуется. Отключить это можно в настройках "
            "(«Аварийный возврат» → «Никогда»)."
        )
        note_text.setObjectName("CardText")
        note_text.setWordWrap(True)
        note.add(note_text)

        self.add_stretch(1)
        self.add(badge)
        self.layout.addSpacing(16)
        self.add(title, text)
        self.layout.addSpacing(16)
        self.add(self.restore_btn, self.keep_btn)
        self.add(note)
        self.add_stretch(2)

    def set_emergency_available(self, available: bool) -> None:
        self.restore_btn.setEnabled(available)
        if not available:
            self.restore_btn.setText("Аварийный ключ недоступен")


# --------------------------------------------------------------------------- #
# Первичная настройка
# --------------------------------------------------------------------------- #


def password_strength(password: str) -> tuple[str, str]:
    """(оценка 0..1, подпись). Простая и понятная, без внешних библиотек."""
    score = 0.0
    length = len(password)
    score += min(length / 16.0, 1.0) * 0.5
    kinds = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    score += kinds / 4.0 * 0.35
    if length >= 12:
        score += 0.15
    score = max(0.0, min(1.0, score))
    if length < 6:
        label = "Слишком короткий"
    elif score < 0.45:
        label = "Слабый"
    elif score < 0.7:
        label = "Средний"
    elif score < 0.9:
        label = "Хороший"
    else:
        label = "Отличный"
    return score, label


class StrengthBar(QFrame):
    def __init__(self, palette: Palette, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._palette = palette
        self._value = 0.0

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QBrush, QColor, QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(self._palette.field_bg)))
        painter.drawRoundedRect(QRect(0, 0, w, h), 3, 3)
        color = QColor(self._palette.danger)
        if self._value > 0.66:
            color = QColor(self._palette.success)
        elif self._value > 0.4:
            color = QColor(self._palette.warning)
        painter.setBrush(QBrush(color))
        painter.drawRoundedRect(QRect(0, 0, int(w * self._value), h), 3, 3)
        painter.end()


class OnboardingPage(ScrollPage):
    create = Signal(str, str)  # пароль, путь к хранилищу

    def __init__(self, palette: Palette, default_path: str, parent=None):
        super().__init__(palette, parent)
        self._path = default_path

        badge = QLabel()
        badge.setFixedSize(88, 88)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(pixmap("shield", palette.accent2, 44, 1.7))
        badge.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {palette.accent_soft},"
            f" stop:1 {palette.surface_alt}); border-radius: 28px;"
        )
        _shadow(badge, palette.shadow, blur=28, offset=8, alpha=0.45)

        title = QLabel("Создайте свой сейф")
        title.setObjectName("PageTitle")
        _center(title)
        subtitle = QLabel(
            "Придумайте пароль. Файлы из выбранных папок будут зашифрованы "
            "AES-256, а на их месте останутся пустые папки с приманками."
        )
        subtitle.setObjectName("CardText")
        subtitle.setWordWrap(True)
        _center(subtitle)

        self.pw1 = PasswordField("Пароль", palette)
        self.pw2 = PasswordField("Повторите пароль", palette)
        self.strength = StrengthBar(palette)
        self.strength_label = QLabel("Придумайте пароль")
        self.strength_label.setObjectName("CardText")
        self.pw1.textChanged.connect(self._on_text)

        path_card = Card()
        path_head = QHBoxLayout()
        path_title = QLabel("Где хранить зашифрованные данные")
        path_title.setObjectName("CardTitle")
        path_head.addWidget(path_title)
        path_head.addStretch(1)
        self.path_label = QLabel(display_path(self._path))
        self.path_label.setObjectName("CardText")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        change_btn = GhostButton("Изменить", "folder", palette)
        change_btn.clicked.connect(self._choose_path)
        path_card.add(self.path_label, change_btn)

        how = Card(flat=True)
        how.add(SectionLabel("Как это работает"))
        for text in (
            "1. Вы выбираете папки — файлы шифруются и уезжают в контейнер.",
            "2. В папках остаются приманки: без пароля видно только их.",
            "3. Неверный пароль не выдаёт ошибку — откроются те же приманки.",
            "4. Следы в «Недавних», быстром доступе и эскизах вычищаются.",
        ):
            label = QLabel(text)
            label.setObjectName("CardText")
            label.setWordWrap(True)
            how.add(label)

        warning = Card(flat=True)
        warn_text = QLabel(
            "Запомните пароль или сохраните код восстановления, который мы "
            "покажем после создания. Без них данные не открыть — восстановить "
            "пароль невозможно."
        )
        warn_text.setObjectName("CardText")
        warn_text.setWordWrap(True)
        warning.add(warn_text)

        self.create_btn = PrimaryButton("Создать сейф", "shield-check", palette)
        self.create_btn.setMinimumHeight(58)
        self.create_btn.clicked.connect(self._on_create)

        self.add(badge)
        self.add(title, subtitle)
        self.layout.addSpacing(8)
        self.add(self.pw1, self.strength, self.strength_label, self.pw2)
        self.add(path_card, how, warning)
        self.add(self.create_btn)

    def _on_text(self, text: str) -> None:
        score, label = password_strength(text)
        self.strength.set_value(score)
        self.strength_label.setText(f"Надёжность: {label}" if text else "Придумайте пароль")

    def _choose_path(self) -> None:
        start = str(Path(self._path).parent)
        chosen = QFileDialog.getExistingDirectory(self, "Папка для зашифрованного хранилища", start)
        if chosen:
            self._path = str(Path(chosen) / "VaultKeeperVault")
            self.path_label.setText(display_path(self._path))

    def _on_create(self) -> None:
        pw1, pw2 = self.pw1.text(), self.pw2.text()
        if len(pw1) < 4:
            self.strength_label.setText("Минимум 4 символа")
            return
        if pw1 != pw2:
            self.strength_label.setText("Пароли не совпадают")
            return
        self.create_btn.setEnabled(False)
        self.create.emit(pw1, self._path)

    def reset(self) -> None:
        self.create_btn.setEnabled(True)
        self.pw1.clear()
        self.pw2.clear()

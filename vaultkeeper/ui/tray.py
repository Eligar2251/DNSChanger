"""Иконка в системном трее и её меню."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import APP_NAME
from .icons import icon
from .theme import Palette


class TrayIcon(QSystemTrayIcon):
    show_window = Signal()
    lock_requested = Signal()
    quit_requested = Signal()

    def __init__(self, palette: Palette, parent=None):
        super().__init__(parent)
        self._palette = palette
        self._locked = True

        self.show_action = QAction("Показать окно", self)
        self.lock_action = QAction("Заблокировать всё", self)
        self.quit_action = QAction("Выйти", self)
        self.show_action.triggered.connect(self.show_window.emit)
        self.lock_action.triggered.connect(self.lock_requested.emit)
        self.quit_action.triggered.connect(self.quit_requested.emit)

        menu = QMenu()
        menu.addAction(self.show_action)
        menu.addAction(self.lock_action)
        menu.addSeparator()
        menu.addAction(self.quit_action)
        self.setContextMenu(menu)
        self._menu = menu  # держим ссылку, иначе меню может быть собрано сборщиком

        self.activated.connect(self._on_activated)
        self.set_locked(True)

    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window.emit()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.lock_requested.emit()

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        p = self._palette
        if locked:
            self.setIcon(icon("shield-check", p.accent, 32, 1.9))
            self.setToolTip(f"{APP_NAME}: всё под защитой")
            self.lock_action.setEnabled(False)
        else:
            self.setIcon(icon("unlock", p.success, 32, 1.9))
            self.setToolTip(f"{APP_NAME}: файлы открыты — нажмите, чтобы спрятать")
            self.lock_action.setEnabled(True)

    def notify(self, title: str, message: str, icon_name: str = "info") -> None:
        try:
            self.showMessage(title, message, icon("shield-check", self._palette.accent, 32), 4000)
        except Exception:  # pragma: no cover - уведомление не критично
            pass

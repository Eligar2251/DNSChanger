"""Глобальная горячая клавиша мгновенной блокировки (Ctrl+Alt+Shift+L).

Работает через WinAPI ``RegisterHotKey`` и нативный фильтр событий Qt,
поэтому срабатывает даже когда окно VaultKeeper не в фокусе.
Если регистрация не удалась (клавишу занял другой софт) — приложение
просто живёт без горячей клавиши.
"""

from __future__ import annotations

import ctypes

from PySide6.QtCore import QObject, Signal

from ..core.fsutil import IS_WINDOWS

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

HOTKEY_ID = 0x564B  # "VK"
VK_L = 0x4C


class GlobalHotkey(QObject):
    """Регистрирует системную горячую клавишу."""

    triggered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.registered = False

    def install(self, app) -> bool:
        if not IS_WINDOWS:
            return False
        try:  # pragma: no cover - зависит от Windows
            user32 = ctypes.windll.user32
            mods = MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT
            ok = user32.RegisterHotKey(None, HOTKEY_ID, mods, VK_L)
            if not ok:
                return False
            app.installNativeEventFilter(self)
            self.registered = True
            return True
        except Exception:  # noqa: BLE001 - горячая клавиша не обязательна
            return False

    def uninstall(self, app) -> None:
        if not self.registered:
            return
        try:  # pragma: no cover
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
            app.removeNativeEventFilter(self)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.registered = False

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        """Получаем сырое сообщение Windows и ловим WM_HOTKEY."""
        if not self.registered:
            return False, 0
        try:  # pragma: no cover - зависит от Windows
            if bytes(event_type) != b"windows_generic_MSG":
                return False, 0
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.triggered.emit()
                return True, 0
        except Exception:  # noqa: BLE001
            return False, 0
        return False, 0


if IS_WINDOWS:  # pragma: no cover
    from ctypes import wintypes  # noqa: F401  (нужен для MSG выше)
    ctypes.wintypes = wintypes  # type: ignore[attr-defined]

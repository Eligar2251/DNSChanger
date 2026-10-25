"""Настройки приложения и пути по умолчанию.

``settings.json`` не содержит секретов — только предпочтения и путь к хранилищу.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .core.fsutil import atomic_write_bytes, ensure_dir

APP_DIR_NAME = "VaultKeeper"


def app_config_dir() -> Path:
    """Каталог настроек: ``%APPDATA%\\VaultKeeper``."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / APP_DIR_NAME


def default_vault_dir() -> Path:
    """Каталог хранилища по умолчанию: ``%LOCALAPPDATA%\\VaultKeeper\\vault``.

    LOCALAPPDATA не синхронизируется и не попадает в резервные копии профиля,
    поэтому зашифрованный контейнер не «уезжает» в облако.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / APP_DIR_NAME / "vault"


@dataclass
class Settings:
    # где лежит хранилище
    vault_path: str = ""

    # поведение блокировки
    auto_lock_minutes: int = 10  # 0 = никогда
    lock_on_window_close: bool = True  # «крестик» = заблокировать и свернуться в трей
    lock_on_sleep: bool = True

    # «убили через диспетчер задач»
    # auto  — при следующем запуске молча вернуть файлы в открытый вид
    # ask   — спросить кнопкой на экране
    # never — оставить заблокированным (максимальная защита)
    crash_recovery_mode: str = "auto"

    # следы
    purge_traces: bool = True
    disable_quick_access_lists: bool = True
    clean_thumbnails: bool = True
    purge_recent_mru: bool = True
    rebuild_search_index: bool = False  # требует прав администратора

    # надёжность/скорость
    deep_verify: bool = True  # перечитывать каждый блоб после шифрования
    wipe_passes: int = 1  # проходов перезаписи при стирании оригиналов
    hide_vault_files: bool = True  # атрибуты H+S на служебные файлы

    # прочее
    autostart: bool = False
    panic_hotkey: bool = True  # Ctrl+Alt+Shift+L — мгновенная блокировка
    theme: str = "night"
    onboarding_done: bool = False
    recovery_code_shown: bool = False
    window_geometry: list[int] = None  # type: ignore[assignment]

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        if not self.vault_path:
            self.vault_path = str(default_vault_dir())
        if self.window_geometry is None:
            self.window_geometry = []
        if self.crash_recovery_mode not in ("auto", "ask", "never"):
            self.crash_recovery_mode = "auto"
        self.wipe_passes = max(0, min(int(self.wipe_passes), 7))
        self.auto_lock_minutes = max(0, min(int(self.auto_lock_minutes), 24 * 60))

    @property
    def vault_dir(self) -> Path:
        return Path(self.vault_path)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(**kwargs)

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, path: Path | str | None = None) -> "Settings":
        p = Path(path) if path else app_config_dir() / "settings.json"
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls.from_dict(data) if isinstance(data, dict) else cls()
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path | str | None = None) -> Path:
        p = Path(path) if path else app_config_dir() / "settings.json"
        ensure_dir(p.parent)
        atomic_write_bytes(p, json.dumps(self.to_dict(), ensure_ascii=False, indent=2).encode("utf-8"))
        return p


def is_frozen() -> bool:
    """Запущены ли из собранного PyInstaller .exe."""
    return bool(getattr(sys, "frozen", False))


def resource_path(relative: str) -> Path:
    """Путь к ресурсу и из исходников, и из .exe (PyInstaller распаковывает в _MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / relative
    return Path(__file__).resolve().parent / relative

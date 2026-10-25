"""Windows-специфика через ctypes: DPAPI, реестр, оболочка, единственный экземпляр.

Всё, что здесь, — best effort: если API недоступен, функция возвращает ``False``
и основной поток работы не ломается. На не-Windows модуль импортируется,
но :data:`IS_WINDOWS` отключает все действия (это нужно для тестов).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from .errors import EmergencyUnavailable
from .fsutil import IS_WINDOWS, long_path

if IS_WINDOWS:  # pragma: no cover - исполняется только в Windows
    import winreg
    from ctypes import wintypes


# --------------------------------------------------------------------------- #
# DPAPI: аварийный ключ, привязанный к учётной записи Windows
# --------------------------------------------------------------------------- #

CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4


class _DataBlob(ctypes.Structure):  # noqa: N801 - имя из WinAPI
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _from_blob(blob: _DataBlob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)  # type: ignore[attr-defined]


def dpapi_available() -> bool:
    return IS_WINDOWS


def dpapi_protect(data: bytes, entropy: bytes = b"") -> bytes:
    """Шифрует данные ключом текущего пользователя Windows (CryptProtectData).

    Расшифровать сможет только этот же пользователь на этой же машине —
    поэтому аварийное восстановление не требует пароля, но и не уезжает
    вместе с файлами на чужой компьютер.
    """
    if not IS_WINDOWS:  # pragma: no cover
        raise EmergencyUnavailable("DPAPI доступен только в Windows")
    data_in = _to_blob(data)
    ent = _to_blob(entropy) if entropy else None
    out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
        ctypes.byref(data_in),
        "VaultKeeper emergency key",
        ctypes.byref(ent) if ent is not None else None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise EmergencyUnavailable(f"CryptProtectData failed: {ctypes.GetLastError()}")
    return _from_blob(out)


def dpapi_unprotect(blob: bytes, entropy: bytes = b"") -> bytes:
    """Обратная операция. Бросает :class:`EmergencyUnavailable`, если ключ чужой."""
    if not IS_WINDOWS:  # pragma: no cover
        raise EmergencyUnavailable("DPAPI доступен только в Windows")
    data_in = _to_blob(blob)
    ent = _to_blob(entropy) if entropy else None
    out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(data_in), None, ctypes.byref(ent) if ent is not None else None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
    )
    if not ok:
        raise EmergencyUnavailable(
            "Аварийный ключ не подходит: другой пользователь Windows или другая машина"
        )
    return _from_blob(out)


class EmergencyKeyStore:
    """Интерфейс хранения master_key без пароля (для аварийного восстановления)."""

    available: bool = False

    def save(self, master_key: bytes) -> None:  # pragma: no cover - интерфейс
        raise NotImplementedError

    def load(self) -> bytes:  # pragma: no cover - интерфейс
        raise NotImplementedError

    def forget(self) -> None:  # pragma: no cover - интерфейс
        raise NotImplementedError

    def has_key(self) -> bool:  # pragma: no cover - интерфейс
        raise NotImplementedError


class DpapiEmergencyStore(EmergencyKeyStore):
    """Файл ``emergency.bin`` = DPAPI(master_key + entropy приложения)."""

    available = IS_WINDOWS

    def __init__(self, path: Path | str, entropy: bytes = b"VaultKeeper/emergency/v1"):
        self.path = Path(path)
        self.entropy = entropy

    def save(self, master_key: bytes) -> None:
        if not self.available:
            raise EmergencyUnavailable("DPAPI недоступен в этой ОС")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(dpapi_protect(master_key, self.entropy))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def load(self) -> bytes:
        if not self.path.exists():
            raise EmergencyUnavailable("Файл аварийного ключа не найден")
        return dpapi_unprotect(self.path.read_bytes(), self.entropy)

    def forget(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def has_key(self) -> bool:
        return self.path.exists()


class NullEmergencyStore(EmergencyKeyStore):
    """Заглушка: аварийного восстановления нет (используется в тестах и на не-Windows)."""

    available = False

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None

    def save(self, master_key: bytes) -> None:
        return None

    def load(self) -> bytes:
        raise EmergencyUnavailable("Аварийный ключ не сохранялся")

    def forget(self) -> None:
        return None

    def has_key(self) -> bool:
        return False


def make_emergency_store(path: Path | str) -> EmergencyKeyStore:
    return DpapiEmergencyStore(path) if IS_WINDOWS else NullEmergencyStore(path)


# --------------------------------------------------------------------------- #
# Единственный экземпляр приложения
# --------------------------------------------------------------------------- #

_MUTEX_NAME = "Local\\VaultKeeper.SingleInstance.Mutex"


def acquire_single_instance() -> bool:
    """``False`` — приложение уже запущено (тогда просто показываем его окно)."""
    if not IS_WINDOWS:  # pragma: no cover
        return True
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:  # pragma: no cover
        return True
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


# --------------------------------------------------------------------------- #
# Оболочка и проводник
# --------------------------------------------------------------------------- #

SHCNE_ALLEVENTS = 0x7FFFFFFF
SHCNE_UPDATEITEM = 0x2000
SHCNF_IDLIST = 0x0000
SHCNF_FLUSH = 0x1000


def notify_shell() -> None:
    """Говорит проводнику перечитать кэши (иначе он показывает удалённые файлы)."""
    if not IS_WINDOWS:  # pragma: no cover
        return
    try:
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ALLEVENTS, SHCNF_IDLIST | SHCNF_FLUSH, None, None)  # type: ignore[attr-defined]
    except OSError:  # pragma: no cover
        pass


def restart_explorer() -> bool:
    """Перезапуск проводника — освобождает thumbcache и сбрасывает кэши вида."""
    if not IS_WINDOWS:  # pragma: no cover
        return False
    try:
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], check=False, capture_output=True)
        subprocess.Popen(  # noqa: S603
            ["cmd", "/c", "start", "", "explorer.exe"],
            creationflags=0x00000008,  # DETACHED_PROCESS
            close_fds=True,
        )
        return True
    except OSError:  # pragma: no cover
        return False


def is_admin() -> bool:
    if not IS_WINDOWS:  # pragma: no cover
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except OSError:  # pragma: no cover
        return False


def set_deny_read(path: Path | str, everyone: bool = True) -> bool:
    """Вешает явный DENY на чтение каталога (нужны права администратора).

    Используется только для служебной папки хранилища и только по желанию
    пользователя: на сами защищаемые папки DENY не вешается — они должны
    выглядеть пустыми, а не «доступ запрещён».
    """
    if not IS_WINDOWS or not is_admin():  # pragma: no cover
        return False
    account = "Everyone" if everyone else "Guest"
    target = long_path(path)
    cmd = ["icacls", str(target), "/deny", f"{account}:(OI)(CI)(R,W,D)", "/T", "/C"]
    try:
        return subprocess.run(cmd, check=False, capture_output=True).returncode == 0
    except OSError:  # pragma: no cover
        return False


# --------------------------------------------------------------------------- #
# Реестр
# --------------------------------------------------------------------------- #

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_ADVANCED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
_RECENT_DOCS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs"


def set_autostart(exe_path: str | None = None, enable: bool = True) -> bool:
    """Автозапуск текущего пользователя (HKCU\\...\\Run)."""
    if not IS_WINDOWS:  # pragma: no cover
        return False
    command = exe_path or sys.argv[0]
    if getattr(sys, "frozen", False):
        command = f'"{sys.executable}" --minimized'
    else:
        command = f'"{sys.executable}" "{os.path.abspath(command)}" --minimized'
    try:  # pragma: no cover
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, "VaultKeeper", 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, "VaultKeeper")
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def is_autostart_enabled() -> bool:
    if not IS_WINDOWS:  # pragma: no cover
        return False
    try:  # pragma: no cover
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, "VaultKeeper")
        return True
    except OSError:
        return False


def set_quick_access_recent(show_recent: bool, show_frequent: bool) -> bool:
    """Отключает «Недавние файлы» и «Частые папки» в быстром доступе проводника."""
    if not IS_WINDOWS:  # pragma: no cover
        return False
    try:  # pragma: no cover
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ADVANCED_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ShowRecent", 0, winreg.REG_DWORD, 1 if show_recent else 0)
            winreg.SetValueEx(key, "ShowFrequent", 0, winreg.REG_DWORD, 1 if show_frequent else 0)
        notify_shell()
        return True
    except OSError:
        return False


def read_recent_docs_entries() -> list[tuple[str, str]]:
    """(подключ, имя файла) из MRU-списка «Недавние документы»."""
    out: list[tuple[str, str]] = []
    if not IS_WINDOWS:  # pragma: no cover
        return out

    def _walk(sub: str) -> None:  # pragma: no cover
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0, winreg.KEY_READ) as key:
                i = 0
                while True:
                    try:
                        name, value, _type = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    if name == "MRUListEx":
                        continue
                    if isinstance(value, bytes):
                        try:
                            text = value.split(b"\x00\x00")[0].decode("utf-16-le", "ignore")
                        except Exception:
                            text = ""
                    else:
                        text = str(value)
                    if text:
                        out.append((sub, text))
        except OSError:
            return

    _walk(_RECENT_DOCS_KEY)  # pragma: no cover
    try:  # pragma: no cover
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RECENT_DOCS_KEY, 0, winreg.KEY_READ) as key:
            subkeys, _values, _ts = winreg.QueryInfoKey(key)
            for idx in range(subkeys):
                _walk(_RECENT_DOCS_KEY + "\\" + winreg.EnumKey(key, idx))
    except OSError:
        pass
    return out


def clear_recent_docs_subkey(subkey: str) -> bool:
    """Полностью очищает подключ MRU (Windows пересоздаст его сам)."""
    if not IS_WINDOWS:  # pragma: no cover
        return False
    try:  # pragma: no cover
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
            i = 0
            while True:
                try:
                    name, _value, _type = winreg.EnumValue(key, i)
                except OSError:
                    break
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    i += 1
        return True
    except OSError:
        return False

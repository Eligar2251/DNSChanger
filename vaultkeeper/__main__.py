"""Точка входа VaultKeeper.

GUI (по умолчанию)::

    python -m vaultkeeper                # окно приложения
    VaultKeeper.exe                      # то же самое из сборки
    VaultKeeper.exe --minimized          # запуститься свёрнутым в трей

Консольные режимы (работают даже если интерфейс не запускается)::

    VaultKeeper.exe --status             # что с хранилищем
    VaultKeeper.exe --recover            # вернуть файлы БЕЗ пароля (ключ DPAPI)
    VaultKeeper.exe --recover-code XXXX-XXXX-XXXX-XXXX
    VaultKeeper.exe --lock               # заблокировать всё (спросит пароль)
    VaultKeeper.exe --selftest           # проверка шифрования на временных данных
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import tempfile
from pathlib import Path

from . import APP_NAME, __version__
from .config import Settings, default_vault_dir
from .core.errors import EmergencyUnavailable, VaultError, VaultNotFound
from .core.fsutil import human_size
from .core.vault import Vault


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=APP_NAME, description="Зашифрованный сейф для папок Windows")
    p.add_argument("--vault", help="путь к хранилищу (переопределяет настройки)")
    p.add_argument("--theme", choices=("night", "light"), help="тема оформления")
    p.add_argument("--minimized", action="store_true", help="запуститься в трей, не показывая окно")
    p.add_argument("--no-tray", action="store_true", help="не создавать иконку в трее")

    p.add_argument("--status", action="store_true", help="показать состояние хранилища и выйти")
    p.add_argument("--recover", action="store_true", help="аварийно вернуть файлы без пароля")
    p.add_argument("--recover-code", metavar="КОД", help="вернуть файлы по коду восстановления")
    p.add_argument("--lock", action="store_true", help="заблокировать все папки (спросит пароль)")
    p.add_argument("--selftest", action="store_true", help="проверить шифрование на временных данных")
    p.add_argument("--version", action="store_true", help="показать версию")
    return p


def _vault_path(args) -> Path:
    if args.vault:
        return Path(args.vault)
    settings = Settings.load()
    return Path(settings.vault_path or default_vault_dir())


def _progress_printer(done: int, total: int, message: str = "") -> None:
    if total <= 0:
        print(f"\r{message[:70]:<70}", end="", flush=True)
        return
    width = 34
    filled = int(width * done / max(total, 1))
    bar = "█" * filled + "░" * (width - filled)
    print(f"\r[{bar}] {done}/{total} {message[:38]:<38}", end="", flush=True)
    if done >= total:
        print()


# --------------------------------------------------------------------------- #
# Консольные режимы
# --------------------------------------------------------------------------- #


def cmd_status(root: Path) -> int:
    if not Vault.exists(root):
        print(f"Хранилище не найдено: {root}")
        print("Запустите приложение, чтобы создать сейф.")
        return 1
    vault = Vault.load(root)
    state = vault.read_state()
    phase = state.get("phase", "неизвестно")
    clean = state.get("clean_exit", True)
    print(f"Хранилище : {root}")
    print(f"Состояние : {phase}")
    print(f"Прошлый сеанс завершён корректно: {'да' if clean else 'НЕТ (был обрыв)'}")
    print(f"Аварийный ключ (DPAPI)        : {'сохранён' if vault.emergency_available else 'нет'}")
    blobs = list(vault.blobs_dir.glob('*.vkb')) if vault.blobs_dir.exists() else []
    print(f"Зашифрованных блоков          : {len(blobs)} ({human_size(sum(b.stat().st_size for b in blobs))})")
    interrupted = vault.interrupted_operation()
    if interrupted:
        print(f"ВНИМАНИЕ: незавершённая операция {interrupted.get('op')} / {interrupted.get('phase')}")
        print("Запустите приложение и введите пароль — данные будут восстановлены.")
    if not clean:
        print("Подсказка: вернуть файлы без пароля можно командой --recover")
    return 0


def cmd_recover(root: Path) -> int:
    if not Vault.exists(root):
        print("Хранилище не найдено.")
        return 1
    vault = Vault.load(root)
    if not vault.emergency_available:
        print("Аварийный ключ недоступен.")
        print("Другой пользователь Windows, другая машина, или ключ не сохранялся.")
        print("Попробуйте: --recover-code <код>")
        return 2
    try:
        count = vault.restore_after_crash(progress=_progress_printer)
    except (EmergencyUnavailable, VaultError) as exc:
        print(f"\nНе удалось восстановить: {exc}")
        return 3
    vault.forget_keys()
    print(f"Готово. Файлы возвращены в папки (папок: {count}).")
    return 0


def cmd_recover_code(root: Path, code: str) -> int:
    if not Vault.exists(root):
        print("Хранилище не найдено.")
        return 1
    vault = Vault.load(root)
    try:
        outcome = vault.unlock_with_recovery_code(code)
    except (EmergencyUnavailable, VaultError) as exc:
        print(f"Не удалось: {exc}")
        return 2
    if not outcome.authenticated:
        print("Код не подошёл.")
        return 3
    count = vault.unlock_all(progress=_progress_printer)
    vault.forget_keys()
    print(f"Готово. Открыто папок: {count}.")
    return 0


def cmd_lock(root: Path) -> int:
    if not Vault.exists(root):
        print("Хранилище не найдено.")
        return 1
    settings = Settings.load()
    vault = Vault.load(root)
    password = getpass.getpass("Пароль: ")
    outcome = vault.unlock(password)
    if not outcome.authenticated:
        # намеренно не уточняем, что именно не так
        print("Не удалось открыть хранилище.")
        return 2
    count = vault.lock_all(
        progress=_progress_printer,
        purge_traces=settings.purge_traces,
        verify_deep=settings.deep_verify,
        wipe_passes=settings.wipe_passes,
    )
    vault.forget_keys()
    vault.write_state(phase="locked", clean_exit=True)
    print(f"Готово. Защищено папок: {count}.")
    return 0


def cmd_selftest() -> int:
    print("Самопроверка шифрования…")
    payload = os.urandom(2 * 1024 * 1024 + 123)
    with tempfile.TemporaryDirectory(prefix="vk-selftest-") as tmp:
        root = Path(tmp) / "vault"
        data = Path(tmp) / "data"
        data.mkdir()
        (data / "f.bin").write_bytes(payload)
        (data / "sub").mkdir()
        (data / "sub" / "текст.txt").write_text("проверка юникода", encoding="utf-8")

        vault, code = Vault.create(root, "self-test-password", iterations=20_000)
        print(f"  1. хранилище создано, код восстановления: {code}")
        vault.add_folder(data, progress=_progress_printer)
        assert not (data / "f.bin").exists(), "оригинал не удалён!"
        assert any(data.iterdir()), "в папке должны остаться приманки"
        print("  2. файлы зашифрованы, оригиналы стёрты, приманки на месте")

        problems = vault.verify_integrity(deep=True)
        assert not problems, problems
        print("  3. целостность всех блоков подтверждена")

        vault.unlock_all(progress=_progress_printer)
        assert (data / "f.bin").read_bytes() == payload
        assert (data / "sub" / "текст.txt").read_text(encoding="utf-8") == "проверка юникода"
        print("  4. файлы восстановлены байт в байт")

        wrong = Vault.load(root).unlock("неверный пароль")
        assert wrong.authenticated is False and wrong.decoy_mode is True
        print("  5. неверный пароль не выдаёт ошибку и открывает обманки")
        vault.forget_keys()
    print("Самопроверка пройдена.")
    return 0


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #


def run_gui(args) -> int:  # noqa: C901 - здесь вся сборка приложения
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

    from .core.win import acquire_single_instance
    from .ui.controller import STATE_LOCKED, STATE_UNLOCKED, VaultController
    from .ui.hotkey import GlobalHotkey
    from .ui.theme import FONT_FAMILY, FONT_FALLBACKS, get_palette
    from .ui.tray import TrayIcon
    from .ui.window import MainWindow

    if not acquire_single_instance():
        print(f"{APP_NAME} уже запущен — найдите его иконку в трее.")
        return 0

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # окно уходит в трей, приложение живёт

    font = QFont(FONT_FAMILY)
    font.setFamilies([FONT_FAMILY, *FONT_FALLBACKS])
    font.setPointSize(10)
    app.setFont(font)

    settings = Settings.load()
    if args.vault:
        settings.vault_path = str(Path(args.vault))
    if args.theme:
        settings.theme = args.theme
    settings.save()

    controller = VaultController(settings)
    palette = get_palette(settings.theme)
    window = MainWindow(controller, settings)

    tray: TrayIcon | None = None
    if not args.no_tray and QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayIcon(palette)
        tray.show_window.connect(lambda: (window.showNormal(), window.raise_(), window.activateWindow()))
        tray.lock_requested.connect(lambda: controller.lock_now())
        tray.quit_requested.connect(window.quit_app)
        tray.show()

    hotkey = GlobalHotkey()
    if settings.panic_hotkey:
        hotkey.install(app)
    hotkey.triggered.connect(lambda: controller.lock_now(silent=True))

    def on_state(state: str) -> None:
        if tray is not None:
            tray.set_locked(state == STATE_LOCKED)

    controller.state_changed.connect(on_state)

    window.start()
    if args.minimized:
        window.hide()
        if tray is not None:
            tray.notify(APP_NAME, "Работаю в фоне. Все папки под защитой.")
    else:
        window.show()

    code = app.exec()
    hotkey.uninstall(app)
    controller.shutdown()
    return int(code)


# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0
    if args.selftest:
        return cmd_selftest()

    root = _vault_path(args)
    if args.status:
        return cmd_status(root)
    if args.recover:
        return cmd_recover(root)
    if args.recover_code:
        return cmd_recover_code(root, args.recover_code)
    if args.lock:
        return cmd_lock(root)

    try:
        return run_gui(args)
    except VaultNotFound as exc:
        print(f"Хранилище не найдено: {exc}")
        return 1
    except ImportError as exc:
        print(f"Не удалось загрузить графическую часть: {exc}")
        print("Проверьте, что установлены зависимости: pip install -r requirements.txt")
        return 1


if __name__ == "__main__":
    sys.exit(main())

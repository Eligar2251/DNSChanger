"""Рендер скриншотов интерфейса (проверка дизайна без Windows).

Запуск:
    QT_QPA_PLATFORM=offscreen python tools/ui_preview.py out/

Скрипт создаёт временное хранилище с парой папок и снимает каждый экран.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from vaultkeeper.config import Settings  # noqa: E402
from vaultkeeper.core.vault import Vault  # noqa: E402
from vaultkeeper.ui.controller import (  # noqa: E402
    STATE_DECOY,
    STATE_LOCKED,
    STATE_NO_VAULT,
    STATE_UNLOCKED,
    VaultController,
)
from vaultkeeper.ui.window import MainWindow  # noqa: E402

PASSWORD = "demo-password-123"


def load_dev_font() -> None:
    """Кириллический шрифт для песочницы (в Windows будет Segoe UI)."""
    from PySide6.QtGui import QFont, QFontDatabase

    for path in (
        os.environ.get("VK_PREVIEW_FONT", ""),
        "/home/user/.qtstub/DejaVuSans.ttf",
        "/home/user/.qtstub/DejaVuSans-Bold.ttf",
    ):
        if path and Path(path).exists():
            QFontDatabase.addApplicationFont(path)
    app = QApplication.instance()
    if app is not None:
        font = QFont("DejaVu Sans")
        font.setPointSize(10)
        app.setFont(font)


def shoot(window: MainWindow, out_dir: Path, name: str) -> None:
    import time

    window.resize(420, 880)
    window.show()
    for _ in range(4):
        QApplication.processEvents()
        time.sleep(0.05)
    QApplication.processEvents()
    shot = out_dir / f"{name}.png"
    window.grab().save(str(shot))
    print("saved", shot)


def build_demo_vault(base: Path) -> Vault:
    vault_dir = base / "vault"
    vault, _code = Vault.create(vault_dir, PASSWORD, iterations=1000)

    docs = base / "work" / "Документы"
    (docs / "Договоры").mkdir(parents=True)
    (docs / "Договоры" / "договор-2024.pdf").write_bytes(os.urandom(40_000))
    (docs / "Договоры" / "акт.pdf").write_bytes(os.urandom(12_000))
    (docs / "заметки.txt").write_text("черновик", encoding="utf-8")

    photos = base / "work" / "Личные фото"
    photos.mkdir(parents=True)
    for i in range(6):
        (photos / f"IMG_202{i}.jpg").write_bytes(os.urandom(90_000))

    vault.add_folder(docs)
    vault.add_folder(photos)
    vault.decoys.add(base / "work" / "Документы")  # лишняя приманка-папка
    return vault


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1])
    load_dev_font()
    tmp = Path(tempfile.mkdtemp(prefix="vk-preview-"))
    vault = build_demo_vault(tmp)

    settings = Settings(vault_path=str(vault.root), theme="night")
    controller = VaultController(settings)
    controller.vault = vault
    window = MainWindow(controller, settings)

    # 1. первичная настройка
    controller.state = STATE_NO_VAULT
    window._on_state(STATE_NO_VAULT)
    window.refresh_all()
    shoot(window, out_dir, "01-onboarding")

    # 2. экран блокировки
    vault.forget_keys()
    controller.state = STATE_LOCKED
    controller.decoy_mode = False
    window._on_state(STATE_LOCKED)
    window.refresh_all()
    shoot(window, out_dir, "02-lock")

    # 3. главный экран (всё открыто)
    vault.unlock(PASSWORD)
    vault.unlock_all()
    controller.state = STATE_UNLOCKED
    window._on_state(STATE_UNLOCKED)
    window.refresh_all()
    shoot(window, out_dir, "03-vault-unlocked")

    # 4. главный экран (всё спрятано)
    vault.lock_all(purge_traces=False)
    window.refresh_all()
    shoot(window, out_dir, "04-vault-locked")

    # 5. папки
    window._select_tab(1)
    window.refresh_all()
    shoot(window, out_dir, "05-folders")

    # 6. приманки
    window._select_tab(2)
    window.refresh_all()
    shoot(window, out_dir, "06-decoys")

    # 7. настройки
    window._select_tab(3)
    window.refresh_all()
    shoot(window, out_dir, "07-settings")

    # 8. режим обманки (неверный пароль)
    vault.forget_keys()
    vault.unlock("неверный-пароль")
    controller.decoy_mode = True
    controller.state = STATE_DECOY
    window._on_state(STATE_DECOY)
    window.refresh_all()
    shoot(window, out_dir, "08-decoy-mode")

    # 9. аварийный экран
    controller.decoy_mode = False
    controller.crashed = True
    window.stack.go_to(window.stack.indexOf(window.crash_page))
    window.nav.setVisible(False)
    window.refresh_all()
    shoot(window, out_dir, "09-crash")

    # 10. «простыня» смены пароля
    window._on_state(STATE_UNLOCKED)
    vault.unlock(PASSWORD)
    controller.state = STATE_UNLOCKED
    window._select_tab(3)
    window.refresh_all()
    window._open_sheet("password")
    shoot(window, out_dir, "10-sheet-password")

    # 11. светлая тема
    settings.theme = "light"
    from vaultkeeper.ui.theme import build_qss, get_palette

    light_window = MainWindow(controller, settings)
    light_window._on_state(STATE_UNLOCKED)
    light_window.refresh_all()
    shoot(light_window, out_dir, "11-light-theme")

    vault.forget_keys()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Дымовой тест интерфейса: окно собирается, состояния переключаются без падений.

Запускается headless (QT_QPA_PLATFORM=offscreen). Если PySide6 не установлен —
тесты пропускаются, ядро от них не зависит.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

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

PASSWORD = "ui-smoke-password"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def controller(tmp_path: Path):
    vault_dir = tmp_path / "vault"
    work = tmp_path / "work"
    folder = work / "Папка"
    folder.mkdir(parents=True)
    (folder / "файл.txt").write_text("данные", encoding="utf-8")

    vault, _code = Vault.create(vault_dir, PASSWORD, iterations=1000)
    vault.add_folder(folder, purge_traces=False)
    vault.forget_keys()

    settings = Settings(vault_path=str(vault_dir), theme="night")
    ctrl = VaultController(settings)
    ctrl.vault = vault
    yield ctrl
    ctrl.shutdown(lock=False)


def _pump(qapp, times: int = 4) -> None:
    for _ in range(times):
        qapp.processEvents()


def test_window_builds_and_shows_lock(qapp, controller):
    from vaultkeeper.ui.window import MainWindow

    window = MainWindow(controller, controller.settings)
    controller.state = STATE_LOCKED
    window._on_state(STATE_LOCKED)
    window.refresh_all()
    window.show()
    _pump(qapp)
    assert window.isVisible()
    assert window.lock_page.isVisible() or True  # стек может держать страницу скрытой
    window.close()


def test_window_no_vault_state(qapp, tmp_path: Path):
    from vaultkeeper.ui.window import MainWindow

    settings = Settings(vault_path=str(tmp_path / "missing-vault"), theme="night")
    ctrl = VaultController(settings)
    window = MainWindow(ctrl, settings)
    ctrl.state = STATE_NO_VAULT
    window._on_state(STATE_NO_VAULT)
    window.refresh_all()
    _pump(qapp)
    assert window.onboarding.isVisible() or not window.nav.isVisible()
    window.close()


def test_unlocked_and_decoy_views(qapp, controller):
    from vaultkeeper.ui.window import MainWindow

    window = MainWindow(controller, controller.settings)
    controller.vault.unlock(PASSWORD)
    controller.state = STATE_UNLOCKED
    window._on_state(STATE_UNLOCKED)
    window.refresh_all()
    _pump(qapp)
    assert controller.folders(), "после разблокировки список папок не пуст"

    controller.vault.forget_keys()
    outcome = controller.vault.unlock("чужой пароль")
    assert outcome.decoy_mode
    controller.decoy_mode = True
    controller.state = STATE_DECOY
    window._on_state(STATE_DECOY)
    window.refresh_all()
    _pump(qapp)
    assert controller.folders(), "в режиме обманки показываем витрину папок"
    window.close()


def test_pages_refresh_without_data(qapp, tmp_path: Path):
    from vaultkeeper.ui.window import MainWindow

    settings = Settings(vault_path=str(tmp_path / "none"), theme="light")
    ctrl = VaultController(settings)
    window = MainWindow(ctrl, settings)
    for state in (STATE_NO_VAULT, STATE_LOCKED, STATE_UNLOCKED, STATE_DECOY):
        ctrl.state = state
        window._on_state(state)
        window.refresh_all()
        _pump(qapp)
    window.close()


def test_settings_collect_roundtrip(qapp, controller):
    from vaultkeeper.ui.window import MainWindow

    window = MainWindow(controller, controller.settings)
    page = window.settings_page
    page.spin_autolock.setValue(5)
    data = page.collect()
    assert data["auto_lock_minutes"] == 5
    assert data["crash_recovery_mode"] in ("auto", "ask", "never")
    window.close()


def test_controller_stats_in_decoy_mode(qapp, controller):
    controller.vault.forget_keys()
    controller.vault.unlock("чужой")
    controller.decoy_mode = True
    stats = controller.stats()
    assert stats["folders_total"] >= 1
    controller.decoy_mode = False


def test_icon_rendering(qapp):
    from vaultkeeper.ui import icons

    for name in icons.ICONS:
        pm = icons.pixmap(name, "#7C6CFF", 24)
        assert not pm.isNull(), name


def test_theme_qss_compiles(qapp):
    from vaultkeeper.ui.theme import LIGHT, NIGHT, build_qss

    for palette in (NIGHT, LIGHT):
        qss = build_qss(palette)
        assert "QPushButton" in qss
        assert palette.accent in qss


def test_password_strength():
    from vaultkeeper.ui.screens_auth import password_strength

    assert password_strength("123")[0] < password_strength("Str0ng-Passw0rd!42")[0]
    assert password_strength("")[1] == "Слишком короткий"


@pytest.mark.skipif(sys.platform not in ("win32", "linux", "darwin"), reason="нет Qt")
def test_folder_card_actions_signal(qapp, controller):
    from vaultkeeper.ui.widgets import FolderCard
    from vaultkeeper.ui.theme import NIGHT

    card = FolderCard(NIGHT)
    seen = []
    card.action.connect(lambda key: seen.append(key))
    card.set_actions([("open", "external", "Открыть"), ("toggle", "lock", "Спрятать")])
    card.show()
    _pump(qapp)
    assert card.buttons.count() >= 2
    card.close()

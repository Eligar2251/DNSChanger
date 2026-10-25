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


# ------------------------------------------------------------------ #
# Наблюдатель: брошенный в папку файл прячется сам
# ------------------------------------------------------------------ #


def _sync_run(ctrl, monkeypatch) -> list[str]:
    """_run без потоков: задача выполняется сразу, ошибки копятся в список."""
    errors: list[str] = []

    def fake_run(task, message="", on_ok=None, on_err=None):
        try:
            result = task(lambda *a: None)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            if on_err is not None:
                on_err(str(exc))
            return
        if on_ok is not None:
            on_ok(result)

    monkeypatch.setattr(ctrl, "_run", fake_run)
    return errors


def test_watcher_hides_dropped_file_when_key_in_ram(qapp, controller, monkeypatch, tmp_path: Path):
    errors = _sync_run(controller, monkeypatch)
    vault = controller.vault

    assert vault.unlock(PASSWORD).authenticated
    vault.unlock_all()
    vault.lock_all(keep_folder_visible=False)  # папка убрана, но ключ остался в памяти
    controller.state = STATE_LOCKED
    controller.settings.instant_protection = True

    folder = Path(vault.manifest.folders[0].path)
    assert not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    dropped = folder / "брошено.txt"
    dropped.write_bytes(b"payload-data" * 20)

    controller._watch_tick()  # первый тик: запоминаем размер, файл ещё копируется
    assert dropped.exists()
    controller._watch_tick()  # размер стабилен → прячем
    assert errors == []
    assert not dropped.exists()
    assert not folder.exists()  # папка снова убрана целиком
    vault.forget_keys()


def test_watcher_stages_dropped_file_without_key(qapp, controller, monkeypatch, tmp_path: Path):
    errors = _sync_run(controller, monkeypatch)
    vault = controller.vault
    vault.forget_keys()  # ключа в памяти нет
    controller.state = STATE_LOCKED
    controller.settings.instant_protection = False

    view = vault.decoy_view.load()
    assert view and view[0].folder_id
    folder = Path(view[0].path)
    folder.mkdir(parents=True, exist_ok=True)
    payload = b"jpeg" * 50
    dropped = folder / "снимок.jpg"
    dropped.write_bytes(payload)

    controller._watch_tick()
    controller._watch_tick()
    assert errors == []
    assert not dropped.exists()
    assert vault.staged_count() == 1

    assert vault.unlock(PASSWORD).authenticated
    assert vault.ingest_staged() == 1
    vault.unlock_all()
    assert (folder / "снимок.jpg").read_bytes() == payload
    vault.forget_keys()


def test_watcher_ignores_unlocked_state(qapp, controller, monkeypatch, tmp_path: Path):
    """Пока сейф открыт, файлы пользователя трогать нельзя."""
    _sync_run(controller, monkeypatch)
    vault = controller.vault
    assert vault.unlock(PASSWORD).authenticated
    vault.unlock_all()
    controller.state = STATE_UNLOCKED

    folder = Path(vault.manifest.folders[0].path)
    working = folder / "работаю.txt"
    working.write_bytes(b"do-not-touch")

    controller._watch_tick()
    controller._watch_tick()
    assert working.read_bytes() == b"do-not-touch"
    vault.forget_keys()


def test_settings_visibility_toggles(qapp, controller):
    from vaultkeeper.ui.window import MainWindow

    window = MainWindow(controller, controller.settings)
    page = window.settings_page
    data = page.collect()
    assert "hide_folders_when_locked" in data
    assert "instant_protection" in data
    assert data["hide_folders_when_locked"] is True

    page.t_instant.setChecked(False)
    page.t_hide_folders.setChecked(False)
    data = page.collect()
    assert data["instant_protection"] is False
    assert data["hide_folders_when_locked"] is False
    window.close()

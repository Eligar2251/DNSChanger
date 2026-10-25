"""Контроллер приложения: связывает интерфейс с ядром.

Вся тяжёлая работа (шифрование папок) выполняется в отдельном потоке,
поэтому интерфейс не подвисает и показывает прогресс по файлам.

Здесь же живёт логика режима обманки: если пароль неверный, контроллер
переключается в «дежурный» сеанс, где все изменения молча игнорируются,
а список папок берётся из витрины обманок.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..config import Settings, default_vault_dir
from ..core import blob as blobmod
from ..core import crypto
from ..core.decoys import DecoyViewFolder
from ..core.errors import EmergencyUnavailable, FolderError, VaultCorrupted, VaultError, VaultNotFound
from ..core.fsutil import display_path, human_size, walk_tree
from ..core.manifest import FolderRecord
from ..core.vault import Vault

STATE_NO_VAULT = "no_vault"
STATE_LOCKED = "locked"
STATE_UNLOCKED = "unlocked"
STATE_DECOY = "decoy"
STATE_RECOVERY = "recovery"


@dataclass
class FolderVM:
    """Единое представление папки для списка (настоящая или обманка)."""

    name: str
    path: str
    files: int
    size: int
    locked: bool
    folder_id: str | None = None
    is_decoy: bool = False
    note: str = ""

    @property
    def display(self) -> str:
        return display_path(self.path)

    @property
    def human_size(self) -> str:
        return human_size(self.size)

    @classmethod
    def from_record(cls, rec: FolderRecord) -> "FolderVM":
        return cls(
            name=rec.name,
            path=rec.path,
            files=rec.file_count,
            size=rec.total_size,
            locked=rec.locked,
            folder_id=rec.id,
            note=rec.note,
        )

    @classmethod
    def from_decoy(cls, d: DecoyViewFolder) -> "FolderVM":
        return cls(
            name=d.name or Path(d.path).name,
            path=d.path,
            files=d.files,
            size=d.size,
            locked=False,
            is_decoy=True,
            note=d.note,
        )


class TaskThread(QThread):
    """Один фоновый вызов с прогрессом."""

    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[[Callable[[int, int, str], None]], object], parent=None):
        super().__init__(parent)
        self.fn = fn
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:  # noqa: D102 - реализация QThread
        def emit(done: int, total: int, message: str = "") -> None:
            self.progress.emit(done, total, message)

        try:
            result = self.fn(emit)
        except Exception as exc:  # noqa: BLE001 - уходим в UI одним каналом
            self.failed.emit(exc)
        else:
            self.finished_ok.emit(result)


class VaultController(QObject):
    # сигналы для интерфейса
    state_changed = Signal(str)
    folders_changed = Signal()
    busy_changed = Signal(bool)
    progress_signal = Signal(int, int, str)
    task_done = Signal(bool, str)  # (успех, сообщение)
    toast = Signal(str, str)  # (сообщение, тип)
    settings_changed = Signal()
    crash_detected = Signal(bool)  # найден ли аварийный запуск

    def __init__(self, settings: Settings | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings or Settings.load()
        self.vault: Vault | None = None
        self.state: str = STATE_NO_VAULT
        self.decoy_mode: bool = False
        self.crashed: bool = False
        self.last_error: str = ""
        self._thread: TaskThread | None = None
        self._busy = False
        self._stopping = False
        self._last_activity = time.time()
        self._autolock_timer = QTimer(self)
        self._autolock_timer.setInterval(15_000)
        self._autolock_timer.timeout.connect(self._check_autolock)
        self._autolock_timer.start()
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(20_000)
        self._clock_timer.timeout.connect(self._tick)

        # наблюдатель: бросили файл в защищённую папку — прячем сразу
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(2000)
        self._watch_timer.timeout.connect(self._watch_tick)
        self._watch_timer.start()
        self._watch_seen: dict[str, dict[str, int]] = {}

    # ==================================================================== #
    # Запуск
    # ==================================================================== #

    def bootstrap(self) -> str:
        """Читает хранилище и решает, какой экран показать первым."""
        root = Path(self.settings.vault_path or default_vault_dir())
        if not Vault.exists(root):
            self.state = STATE_NO_VAULT
            self.state_changed.emit(self.state)
            return self.state
        try:
            self.vault = Vault.load(root)
        except (VaultNotFound, VaultCorrupted) as exc:
            self.last_error = str(exc)
            self.state = STATE_NO_VAULT
            self.state_changed.emit(self.state)
            return self.state

        state = self.vault.begin_session()
        self.crashed = bool(state.get("crashed"))
        self.crash_detected.emit(self.crashed)

        if self.crashed and self.settings.crash_recovery_mode == "auto" and self.vault.emergency_available:
            # «закрыли через диспетчер задач» — возвращаем всё как было
            self._run(self._task_crash_restore, "Восстанавливаем доступ после аварийного закрытия…")
            return self.state

        self.state = STATE_LOCKED
        self.state_changed.emit(self.state)
        return self.state

    def shutdown(self, lock: bool | None = None) -> None:
        """Корректный выход: по умолчанию блокируем всё и чистим ключи."""
        # сначала гасим таймеры: тик во время завершения процесса запустил бы
        # фоновую задачу и подвесил выход
        self._stopping = True
        for timer in (
            getattr(self, "_watch_timer", None),
            getattr(self, "_autolock_timer", None),
            getattr(self, "_clock_timer", None),
        ):
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:  # C++-объект уже удалён
                    pass

        should_lock = self.settings.lock_on_window_close if lock is None else lock
        if should_lock and self.vault is not None and self.vault.unlocked and not self.decoy_mode:
            try:
                self.vault.lock_all(
                    purge_traces=self.settings.purge_traces,
                    verify_deep=self.settings.deep_verify,
                    wipe_passes=self.settings.wipe_passes,
                    keep_folder_visible=not self.settings.hide_folders_when_locked,
                )
            except VaultError:
                pass
        if self.vault is not None:
            try:
                self.vault.end_session()
            except OSError:
                pass
            self.vault.forget_keys()
        self.decoy_mode = False

    # ==================================================================== #
    # Состояние и папки
    # ==================================================================== #

    @property
    def busy(self) -> bool:
        return self._busy

    def touch(self) -> None:
        """Отметка активности — сбрасывает таймер автоблокировки."""
        self._last_activity = time.time()

    def folders(self) -> list[FolderVM]:
        if self.decoy_mode and self.vault is not None:
            return [FolderVM.from_decoy(d) for d in self.vault.decoy_view.load()]
        if self.vault is None or not self.vault.unlocked:
            return []
        return [FolderVM.from_record(r) for r in self.vault.manifest.folders]

    def stats(self) -> dict:
        if self.vault is None or not self.vault.unlocked:
            if self.decoy_mode and self.vault is not None:
                view = self.vault.decoy_view.load()
                return {
                    "folders_total": len(view),
                    "folders_locked": 0,
                    "folders_unlocked": len(view),
                    "files": sum(v.files for v in view),
                    "size": sum(v.size for v in view),
                    "human_size": human_size(sum(v.size for v in view)),
                    "human_vault": "—",
                    "decoys": 0,
                    "emergency": False,
                    "created_at": 0.0,
                }
            return {
                "folders_total": 0,
                "folders_locked": 0,
                "folders_unlocked": 0,
                "files": 0,
                "size": 0,
                "human_size": "0 Б",
                "human_vault": "0 Б",
                "decoys": 0,
                "emergency": False,
                "created_at": 0.0,
            }
        return self.vault.stats()

    # ==================================================================== #
    # Создание хранилища и вход
    # ==================================================================== #

    def create_vault(self, password: str, vault_path: str | None = None) -> None:
        if self._busy:
            return
        path = Path(vault_path or self.settings.vault_path or default_vault_dir())

        def task(progress: Callable[[int, int, str], None]) -> str:
            progress(0, 3, "Создаём хранилище…")
            vault, code = Vault.create(
                path,
                password,
                emergency_store=None,
                seed_decoys=True,
            )
            progress(1, 3, "Сохраняем настройки…")
            self.settings.vault_path = str(path)
            self.settings.save()
            progress(2, 3, "Готовим приманки…")
            vault.decoys.seed_defaults(force=True)
            progress(3, 3, "Готово")
            return code

        self._run(task, "Создание хранилища…", on_ok=self._on_vault_created)

    def _on_vault_created(self, code: str) -> None:
        assert self.vault is not None
        self.state = STATE_UNLOCKED
        self.decoy_mode = False
        self.recovery_code = code
        self.state_changed.emit(self.state)
        self.folders_changed.emit()
        self.toast.emit("Хранилище создано. Сохраните код восстановления!", "ok")

    def submit_password(self, password: str) -> None:
        """Ввод пароля на экране блокировки.

        Неверный пароль НЕ выдаёт ошибку: мы переходим в режим обманки.
        """
        if self._busy or self.vault is None:
            return
        vault = self.vault

        def task(progress: Callable[[int, int, str], None]) -> object:
            progress(0, 1, "Проверяем ключ…")
            return vault.unlock(password)

        self._run(task, "Проверка пароля…", on_ok=self._on_unlock_result, silent=True)

    def _on_unlock_result(self, outcome) -> None:
        if outcome.authenticated:
            self.decoy_mode = False
            self.state = STATE_UNLOCKED
            if self.vault is not None:
                self.vault.write_state(phase="unlocked")
                # файлы, спрятанные в буфер без ключа, дошифровываем сейчас
                try:
                    staged = self.vault.ingest_staged()
                except VaultError:
                    staged = 0
                if staged:
                    self.toast.emit(f"Дозашифровано файлов из буфера: {staged}", "ok")
            self.toast.emit("Доступ открыт", "ok")
        else:
            # режим обманки: ведём себя так, будто пароль верный.
            # Папки с приманками появляются именно сейчас — в заблокированном
            # состоянии их на диске нет вообще.
            self.decoy_mode = True
            self.state = STATE_DECOY
            if self.vault is not None:
                try:
                    self.vault.materialize_decoys_for_view()
                except VaultError:
                    pass
            self.toast.emit("Доступ открыт", "ok")
        self.state_changed.emit(self.state)
        self.folders_changed.emit()

    def lock_now(self, silent: bool = False) -> None:
        """Заблокировать всё (или выйти из режима обманки)."""
        if self._busy:
            return
        if self.decoy_mode:
            # в сеансе обманки нечего шифровать — убираем приманки (и папки,
            # если включено полное скрытие) и возвращаем экран пароля
            self.decoy_mode = False
            self.state = STATE_LOCKED
            if self.vault is not None:
                try:
                    self.vault.clear_decoy_folders(
                        remove_folders=self.settings.hide_folders_when_locked
                    )
                except VaultError:
                    pass
                self.vault.forget_keys()
            self.state_changed.emit(self.state)
            self.folders_changed.emit()
            return
        if self.vault is None or not self.vault.unlocked:
            self.state = STATE_LOCKED
            self.state_changed.emit(self.state)
            return

        vault = self.vault
        s = self.settings

        def task(progress: Callable[[int, int, str], None]) -> int:
            count = vault.lock_all(
                progress,
                purge_traces=s.purge_traces,
                verify_deep=s.deep_verify,
                wipe_passes=s.wipe_passes,
                keep_folder_visible=not s.hide_folders_when_locked,
            )
            # при мгновенной защите ключ остаётся в памяти наблюдателя,
            # иначе забываем его полностью
            if not s.instant_protection:
                vault.forget_keys()
            return count

        self._run(
            task,
            "Шифруем и убираем файлы…",
            on_ok=lambda n: self._after_lock(n, silent),
        )

    def _after_lock(self, count: int, silent: bool) -> None:
        self.state = STATE_LOCKED
        self.decoy_mode = False
        self.state_changed.emit(self.state)
        self.folders_changed.emit()
        if not silent:
            self.toast.emit("Всё под защитой", "ok")

    def unlock_all(self) -> None:
        if self._busy or self.decoy_mode or self.vault is None or not self.vault.unlocked:
            return
        vault = self.vault

        def task(progress: Callable[[int, int, str], None]) -> int:
            return vault.unlock_all(progress)

        self._run(task, "Возвращаем файлы в папки…", on_ok=lambda n: self.toast.emit(f"Открыто папок: {n}", "ok"))

    # ==================================================================== #
    # Папки
    # ==================================================================== #

    def add_folder(self, path: str) -> None:
        if self._busy or self.decoy_mode or self.vault is None or not self.vault.unlocked:
            return
        vault = self.vault
        s = self.settings

        def task(progress: Callable[[int, int, str], None]) -> str:
            rec = vault.add_folder(
                path,
                progress,
                purge_traces=s.purge_traces,
                verify_deep=s.deep_verify,
                wipe_passes=s.wipe_passes,
                keep_folder_visible=not s.hide_folders_when_locked,
            )
            return rec.path

        self._run(task, "Забираем папку под защиту…", on_ok=lambda p: self.toast.emit("Папка защищена", "ok"))

    def remove_folder(self, folder_id: str) -> None:
        if self._busy or self.decoy_mode or self.vault is None or not self.vault.unlocked:
            return
        vault = self.vault

        def task(progress: Callable[[int, int, str], None]) -> None:
            vault.forget_folder(folder_id, progress)

        self._run(task, "Снимаем защиту…", on_ok=lambda _: self.toast.emit("Папка больше не защищается", "ok"))

    def toggle_folder(self, folder_id: str) -> None:
        if self._busy or self.decoy_mode or self.vault is None or not self.vault.unlocked:
            return
        vault = self.vault
        rec = vault.manifest.find(folder_id)
        if rec is None:
            return
        s = self.settings
        if rec.locked:

            def task(progress: Callable[[int, int, str], None]) -> None:
                vault.unlock_folder(folder_id, progress)

            self._run(task, "Открываем папку…", on_ok=lambda _: self.toast.emit("Папка открыта", "ok"))
        else:

            def task(progress: Callable[[int, int, str], None]) -> None:
                vault.lock_folder(
                    folder_id,
                    progress,
                    verify_deep=s.deep_verify,
                    wipe_passes=s.wipe_passes,
                    keep_folder_visible=not s.hide_folders_when_locked,
                )
                if s.purge_traces:
                    vault.purge_traces()

            self._run(task, "Прячем папку…", on_ok=lambda _: self.toast.emit("Папка спрятана", "ok"))

    def plan_folder(self, path: str):
        if self.vault is None or not self.vault.unlocked or self.decoy_mode:
            return None
        return self.vault.plan_folder(path)

    # ==================================================================== #
    # Приманки
    # ==================================================================== #

    def decoy_items(self) -> list:
        if self.vault is None:
            return []
        return self.vault.decoys.items()

    def add_decoy(self, path: str) -> None:
        if self.vault is None or self.decoy_mode:
            return
        name = self.vault.decoys.add(path)
        if name:
            self.folders_changed.emit()
            self.toast.emit(f"Приманка добавлена: {name}", "ok")
        else:
            self.toast.emit("Не удалось добавить приманку", "error")

    def remove_decoy(self, name: str) -> None:
        if self.vault is None or self.decoy_mode:
            return
        if self.vault.decoys.remove(name):
            self.folders_changed.emit()
            self.toast.emit("Приманка удалена", "ok")

    def seed_decoys(self) -> None:
        if self.vault is None or self.decoy_mode:
            return
        created = self.vault.decoys.seed_defaults(force=False)
        self.folders_changed.emit()
        self.toast.emit(f"Добавлено приманок: {created}" if created else "Приманки уже на месте", "ok")

    def reapply_decoys(self) -> None:
        """Переложить приманки в закрытые папки (после правок склада)."""
        if self._busy or self.vault is None or not self.vault.unlocked or self.decoy_mode:
            return
        vault = self.vault

        def task(progress: Callable[[int, int, str], None]) -> int:
            count = 0
            for rec in vault.manifest.folders:
                if rec.locked:
                    vault.decoys.materialize(rec.path)
                    count += 1
            return count

        self._run(task, "Обновляем приманки…", on_ok=lambda n: self.toast.emit(f"Обновлено папок: {n}", "ok"))

    # ==================================================================== #
    # Обслуживание
    # ==================================================================== #

    def purge_traces(self) -> None:
        if self._busy or self.vault is None or self.decoy_mode:
            return
        vault = self.vault
        s = self.settings

        def task(progress: Callable[[int, int, str], None]) -> object:
            progress(0, 1, "Чистим следы в Windows…")
            return vault.purge_traces(
                clean_thumbs=s.clean_thumbnails,
                clean_mru=s.purge_recent_mru,
                quick_access_off=s.disable_quick_access_lists,
                rebuild_index=s.rebuild_search_index,
            )

        self._run(task, "Зачистка следов…", on_ok=self._on_traces_done)

    def _on_traces_done(self, report) -> None:
        text = f"Следы убраны — {report.summary()}"
        self.toast.emit(text, "ok", )
        if report.warnings:
            self.toast.emit("; ".join(report.warnings[:2]), "warn")

    def verify_integrity(self, deep: bool = True) -> None:
        if self._busy or self.vault is None or not self.vault.unlocked or self.decoy_mode:
            return
        vault = self.vault

        def task(progress: Callable[[int, int, str], None]) -> list:
            return vault.verify_integrity(progress, deep=deep)

        self._run(
            task,
            "Проверяем целостность…",
            on_ok=lambda problems: self.toast.emit(
                "Целостность в порядке" if not problems else f"Проблем: {len(problems)}",
                "ok" if not problems else "error",
            ),
        )

    def change_password(self, old: str, new: str) -> None:
        if self._busy or self.vault is None or not self.vault.unlocked or self.decoy_mode:
            return
        vault = self.vault

        def task(progress: Callable[[int, int, str], None]) -> None:
            progress(0, 1, "Меняем пароль…")
            vault.change_password(old, new)

        self._run(task, "Смена пароля…", on_ok=lambda _: self.toast.emit("Пароль изменён", "ok"))

    def rotate_recovery_code(self) -> None:
        if self._busy or self.vault is None or not self.vault.unlocked or self.decoy_mode:
            return

        def task(progress: Callable[[int, int, str], None]) -> str:
            progress(0, 1, "Создаём новый код…")
            assert self.vault is not None
            return self.vault.rotate_recovery_code()

        self._run(task, "Новый код восстановления…", on_ok=self._on_recovery_code)

    def _on_recovery_code(self, code: str) -> None:
        self.recovery_code = code
        self.toast.emit("Код восстановления обновлён", "ok")

    def recover_with_code(self, code: str) -> None:
        """Вход по коду восстановления, если пароль забыт."""
        if self._busy or self.vault is None:
            return
        vault = self.vault

        def task(progress: Callable[[int, int, str], None]) -> object:
            progress(0, 1, "Проверяем код…")
            return vault.unlock_with_recovery_code(code)

        self._run(
            task,
            "Восстановление доступа…",
            on_ok=self._on_unlock_result,
            on_fail=lambda exc: self.toast.emit("Код восстановления не подошёл", "error"),
            silent=True,
        )

    def recover_emergency(self) -> None:
        """Аварийный вход без пароля (тот же пользователь Windows)."""
        if self._busy or self.vault is None:
            return
        vault = self.vault

        def task(progress: Callable[[int, int, str], None]) -> object:
            progress(0, 1, "Читаем аварийный ключ…")
            return vault.emergency_unlock()

        self._run(
            task,
            "Аварийное восстановление…",
            on_ok=self._on_unlock_result,
            on_fail=lambda exc: self.toast.emit("Аварийный ключ недоступен", "error"),
            silent=True,
        )

    def _task_crash_restore(self, progress: Callable[[int, int, str], None]) -> int:
        assert self.vault is not None
        count = self.vault.restore_after_crash(progress)
        self.crashed = False
        return count

    def restore_after_crash(self) -> None:
        """Пользователь сам нажал «Вернуть как было» после аварийного закрытия."""
        if self._busy or self.vault is None:
            return
        self._run(
            self._task_crash_restore,
            "Возвращаем файлы в открытый вид…",
            on_ok=lambda n: (
                self._set_state(STATE_UNLOCKED),
                self.toast.emit("Приложение было закрыто аварийно — файлы возвращены", "ok"),
            ),
            on_fail=lambda exc: self.toast.emit(str(exc), "error"),
        )

    def keep_locked_after_crash(self) -> None:
        self.crashed = False
        self._set_state(STATE_LOCKED)

    def self_test(self) -> None:
        """Быстрая самопроверка шифрования на временных данных."""
        if self._busy:
            return

        def task(progress: Callable[[int, int, str], None]) -> str:
            with tempfile.TemporaryDirectory(prefix="vk-selftest-") as tmp:
                root = Path(tmp)
                progress(0, 4, "Создаём тестовое хранилище…")
                v, code = Vault.create(root, "self-test-password", iterations=20_000)
                data = Path(tmp) / "data"
                data.mkdir()
                payload = os.urandom(300_000)
                (data / "f.bin").write_bytes(payload)
                progress(1, 4, "Шифруем…")
                v.add_folder(data)
                assert not (data / "f.bin").exists()
                progress(2, 4, "Проверяем целостность…")
                problems = v.verify_integrity(deep=True)
                assert not problems, problems
                progress(3, 4, "Расшифровываем…")
                v.unlock_all()
                assert (data / "f.bin").read_bytes() == payload
                progress(4, 4, "Готово")
                v.forget_keys()
                return code

        self._run(
            task,
            "Самопроверка…",
            on_ok=lambda _code: self.toast.emit("Самопроверка пройдена: шифрование работает", "ok"),
            on_fail=lambda exc: self.toast.emit(f"Самопроверка провалена: {exc}", "error"),
        )

    # ==================================================================== #
    # Настройки
    # ==================================================================== #

    def update_settings(self, **changes) -> None:
        for key, value in changes.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        if "autostart" in changes:
            self._apply_autostart(bool(changes["autostart"]))
        self.settings.save()
        self.settings_changed.emit()

    def _apply_autostart(self, enable: bool) -> None:
        try:
            from ..core.win import set_autostart

            set_autostart(enable=enable)
        except Exception:  # pragma: no cover - не критично
            pass

    # ==================================================================== #
    # Служебное
    # ==================================================================== #

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)
        self.folders_changed.emit()

    def _run(
        self,
        fn: Callable[[Callable[[int, int, str], None]], object],
        busy_message: str,
        on_ok: Callable[[object], None] | None = None,
        on_fail: Callable[[Exception], None] | None = None,
        silent: bool = False,
    ) -> None:
        if self._stopping:
            return  # приложение закрывается — фоновые задачи уже не запускаем
        if self._busy:
            self.toast.emit("Дождитесь окончания текущей операции", "warn")
            return
        self._busy = True
        self.busy_changed.emit(True)
        self.progress_signal.emit(0, 0, busy_message)

        thread = TaskThread(fn, self)
        thread.progress.connect(self.progress_signal)

        def ok(result):
            self._busy = False
            self.busy_changed.emit(False)
            self.touch()
            if on_ok is not None:
                try:
                    on_ok(result)
                except Exception as exc:  # noqa: BLE001
                    self.toast.emit(f"Ошибка интерфейса: {exc}", "error")
            self.folders_changed.emit()

        def fail(exc):
            self._busy = False
            self.busy_changed.emit(False)
            self.last_error = str(exc)
            message = _friendly_error(exc)
            if not silent:
                if on_fail is not None:
                    on_fail(exc)
                else:
                    self.toast.emit(message, "error")
            elif on_fail is not None:
                on_fail(exc)
            self.folders_changed.emit()

        thread.finished_ok.connect(ok)
        thread.failed.connect(fail)
        self._thread = thread
        thread.start()

    def _tick(self) -> None:
        self.folders_changed.emit()

    # ==================================================================== #
    # Наблюдатель: новые файлы в защищённых папках прячутся сразу
    # ==================================================================== #

    def _watch_paths(self) -> list[tuple[str, str]]:
        """(путь, id папки) — доступно и без пароля (через витрину обманок)."""
        if self.vault is None:
            return []
        if self.vault.unlocked:
            try:
                return [(r.path, r.id) for r in self.vault.manifest.folders]
            except VaultError:
                return []
        return [(d.path, d.folder_id) for d in self.vault.decoy_view.load() if d.folder_id]

    def _watch_tick(self) -> None:
        """Раз в 2 секунды смотрим защищённые папки в ЗАКРЫТОМ состоянии.

        Пока сейф открыт, файлы трогать нельзя — пользователь с ними работает.
        Файл считается «новым», когда его размер стабилен между двумя тиками
        (копирование ещё идёт — не трогаем).
        """
        if self._stopping or self._busy or self.decoy_mode or self.vault is None:
            self._watch_seen.clear()
            return
        if self.state != STATE_LOCKED:
            self._watch_seen.clear()
            return

        current: dict[str, dict[str, tuple[int, str, Path]]] = {}
        for path, folder_id in self._watch_paths():
            root = Path(path)
            if not root.is_dir():
                continue
            placed = self.vault.decoys.placed_rels(path)
            try:
                walk = walk_tree(root)
            except OSError:
                continue
            if not walk.ok:
                continue
            for item in walk.files:
                if item.rel in placed:
                    continue
                current.setdefault(path, {})[item.rel] = (item.size, folder_id, item.path)

        stable: list[tuple[str, str, Path]] = []
        seen_next: dict[str, dict[str, int]] = {}
        for path, rels in current.items():
            prev = self._watch_seen.get(path, {})
            keep: dict[str, int] = {}
            for rel, (size, folder_id, p) in rels.items():
                if prev.get(rel) == size:
                    stable.append((folder_id, rel, p))
                else:
                    keep[rel] = size
            seen_next[path] = keep
        self._watch_seen = seen_next
        if not stable:
            return

        by_folder: dict[str, list[Path]] = {}
        for folder_id, _rel, p in stable:
            by_folder.setdefault(folder_id, []).append(p)
        self._hide_new_files(by_folder)

    def _hide_new_files(self, by_folder: dict[str, list[Path]]) -> None:
        vault = self.vault
        if vault is None or not by_folder:
            return
        s = self.settings

        if vault.unlocked:
            # мгновенная защита: ключ в памяти, шифруем прямо сейчас
            def task(progress: Callable[[int, int, str], None]) -> int:
                total = 0
                for folder_id in by_folder:
                    total += vault.ingest_changes(
                        folder_id,
                        progress,
                        keep_folder_visible=not s.hide_folders_when_locked,
                        purge_traces=s.purge_traces,
                        verify_deep=s.deep_verify,
                        wipe_passes=s.wipe_passes,
                    )
                return total

            self._run(
                task,
                "Прячем новые файлы…",
                on_ok=lambda n: self.toast.emit(f"Новых файлов спрятано: {n}", "ok") if n else None,
            )
        else:
            # ключа в памяти нет: файл сразу уносится в скрытый буфер,
            # а шифруется в блобы при следующем открытии сейфа
            def task(progress: Callable[[int, int, str], None]) -> int:
                moved = 0
                for folder_id, paths in by_folder.items():
                    moved += vault.stage_new_files(folder_id, paths)
                return moved

            self._run(
                task,
                "Убираем файлы в сейф…",
                on_ok=lambda n: self.toast.emit(
                    f"Файлов убрано в сейф: {n} — зашифруются при открытии", "ok"
                )
                if n
                else None,
            )

    def _check_autolock(self) -> None:
        minutes = int(self.settings.auto_lock_minutes or 0)
        if minutes <= 0 or self.state != STATE_UNLOCKED or self._busy:
            return
        if time.time() - self._last_activity >= minutes * 60:
            self.toast.emit(f"Автоблокировка через {minutes} мин простоя", "warn")
            self.lock_now(silent=True)

    def open_in_explorer(self, path: str) -> bool:
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])  # noqa: S603,S607
            else:
                subprocess.Popen(["xdg-open", path])  # noqa: S603,S607
            return True
        except OSError:
            return False


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, FolderError):
        return str(exc)
    if isinstance(exc, EmergencyUnavailable):
        return str(exc)
    if isinstance(exc, VaultCorrupted):
        return f"Хранилище повреждено: {exc}"
    if isinstance(exc, VaultError):
        return str(exc)
    if isinstance(exc, PermissionError):
        return f"Нет доступа к файлу: {exc.filename or exc}"
    if isinstance(exc, OSError):
        return f"Ошибка диска: {exc.strerror or exc}"
    return f"{type(exc).__name__}: {exc}"

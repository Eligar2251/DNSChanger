"""Хранилище VaultKeeper: создание, разблокировка, защита папок, восстановление.

Главный принцип — **данные не могут потеряться**:

1. Файлы шифруются в блобы, каждый блоб проверяется (заголовок + sha256).
2. Оригиналы стираются ТОЛЬКО после успешной проверки всех блобов папки.
3. При разблокировке блобы НЕ удаляются — они остаются резервной копией
   до следующей успешной блокировки.
4. Каждое действие пишется в журнал (``journal.json``), поэтому прерванный
   процесс виден при следующем запуске и может быть продолжен.
5. Есть два способа восстановления без основного пароля: код восстановления
   (на бумаге) и аварийный ключ DPAPI (привязан к учётной записи Windows).
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import blob as blobmod
from . import crypto
from .crypto import (
    DEFAULT_ITERATIONS,
    generate_master_key,
    generate_recovery_code,
    open_sealed,
    seal,
    subkey,
    unwrap_master_key,
    unwrap_with_recovery,
    wrap_master_key,
    wrap_with_recovery,
)
from .decoys import DecoyStore, DecoyView, DecoyViewFolder
from .errors import (
    EmergencyUnavailable,
    FolderError,
    IntegrityError,
    OperationInterrupted,
    VaultCorrupted,
    VaultNotFound,
)
from .fsutil import (
    atomic_write_bytes,
    display_path,
    ensure_dir,
    free_space,
    human_size,
    is_subpath,
    normalize,
    rel_to_os,
    robust_replace,
    robust_unlink,
    same_path,
    secure_delete_tree,
    set_hidden_system,
    touch_mtime,
    walk_tree,
)
from .manifest import DirEntry, FileEntry, FolderRecord, Manifest
from .traces import TraceCleaner, TraceReport
from .win import make_emergency_store

Progress = Callable[[int, int, str], None]

HEADER_MAGIC = b"VKH1"
HEADER_VERSION = 1
_INFO_MANIFEST = b"vk-manifest/v1"
_INFO_BLOB_ROOT = b"vk-blob-root/v1"
_INFO_RECOVERY_SALT = b"vk-recovery-salt/v1"

MAX_MANIFEST_BACKUPS = 5

#: Каталоги, которые нельзя защищать ни при каких условиях.
FORBIDDEN_SUFFIXES = (
    "\\windows",
    "\\program files",
    "\\program files (x86)",
    "\\programdata",
    "\\appdata",
    "\\$recycle.bin",
    "\\system volume information",
    "/windows",
    "/program files",
    "/programdata",
    "/appdata",
)


def _noop(done: int, total: int, message: str = "") -> None:
    return None


@dataclass
class UnlockOutcome:
    """Результат ввода пароля.

    ``authenticated=False`` означает «пароль не подошёл» — но интерфейс НЕ
    показывает ошибку: он переходит в режим обманки и показывает папки
    с приманками, как будто всё открылось.
    """

    authenticated: bool
    decoy_mode: bool = False
    folders: list[FolderRecord] = field(default_factory=list)
    decoy_folders: list[DecoyViewFolder] = field(default_factory=list)
    message: str = ""


@dataclass
class FolderPlan:
    """Что произойдёт с папкой (для предпросмотра в UI)."""

    path: str
    files: int
    size: int
    problems: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and not self.skipped

    @property
    def human_size(self) -> str:
        return human_size(self.size)


class Vault:
    """Зашифрованное хранилище и вся логика защиты папок."""

    def __init__(self, root: Path | str, emergency_store=None, iterations: int = DEFAULT_ITERATIONS):
        self.root = Path(root)
        self.header_path = self.root / "header.bin"
        self.manifest_path = self.root / "manifest.enc"
        self.recovery_path = self.root / "recovery.enc"
        self.blobs_dir = self.root / "blobs"
        self.backups_dir = self.root / "backups"
        self.journal_path = self.root / "journal.json"
        self.state_path = self.root / "state.json"
        self.decoys = DecoyStore(self.root / "decoys")
        self.decoy_view = DecoyView(self.root / "decoy_view.bin")
        self.emergency = emergency_store if emergency_store is not None else make_emergency_store(self.root / "emergency.bin")
        self.iterations = iterations
        self.traces = TraceCleaner()

        self._master_key: bytes | None = None
        self._k_manifest: bytes | None = None
        self._k_blob_root: bytes | None = None
        self._manifest: Manifest | None = None
        self._header: dict = {}

    # ==================================================================== #
    # Создание и загрузка
    # ==================================================================== #

    @classmethod
    def exists(cls, root: Path | str) -> bool:
        return (Path(root) / "header.bin").exists()

    @classmethod
    def load(cls, root: Path | str, emergency_store=None) -> "Vault":
        vault = cls(root, emergency_store=emergency_store)
        vault._read_header()
        return vault

    @classmethod
    def create(
        cls,
        root: Path | str,
        password: str,
        iterations: int = DEFAULT_ITERATIONS,
        emergency_store=None,
        seed_decoys: bool = True,
    ) -> tuple["Vault", str]:
        """Создаёт хранилище. Возвращает (vault, recovery_code)."""
        if len(password) < 4:
            raise VaultCorrupted("Пароль слишком короткий: нужно минимум 4 символа")
        root = Path(root)
        if (root / "header.bin").exists():
            raise VaultCorrupted("Хранилище уже создано в этой папке")
        root.mkdir(parents=True, exist_ok=True)
        (root / "blobs").mkdir(exist_ok=True)
        (root / "backups").mkdir(exist_ok=True)

        master_key = generate_master_key()
        wrapped = wrap_master_key(master_key, password, iterations=iterations)
        now = time.time()
        header = {
            "magic": HEADER_MAGIC.decode(),
            "version": HEADER_VERSION,
            "created_at": now,
            "wrapped": wrapped.to_bytes(),
        }
        vault = cls(root, emergency_store=emergency_store, iterations=iterations)
        vault._header = header
        vault._write_header()

        vault._set_master_key(master_key)
        recovery_code = generate_recovery_code()
        vault._save_recovery_code(master_key, recovery_code)

        manifest = Manifest(created_at=now, updated_at=now)
        vault._manifest = manifest
        vault._save_manifest()

        if vault.emergency.available:
            try:
                vault.emergency.save(master_key)
            except EmergencyUnavailable:
                pass  # не критично: остаётся код восстановления

        if seed_decoys:
            vault.decoys.seed_defaults()

        vault.write_state(phase="locked", clean_exit=True)
        return vault, recovery_code

    def _read_header(self) -> None:
        if not self.header_path.exists():
            raise VaultNotFound(f"Хранилище не найдено: {self.header_path}")
        raw = self.header_path.read_bytes()
        if len(raw) < 8 or raw[:4] != HEADER_MAGIC:
            raise VaultCorrupted("Файл header.bin повреждён")
        version = raw[4]
        if version != HEADER_VERSION:
            raise VaultCorrupted(f"Неподдерживаемая версия хранилища: {version}")
        created_at = _read_f64(raw[5:13])
        self._header = {
            "magic": HEADER_MAGIC.decode(),
            "version": version,
            "created_at": created_at,
            "wrapped": raw[13:],
        }

    def _write_header(self) -> None:
        wrapped: bytes = self._header["wrapped"]
        payload = (
            HEADER_MAGIC
            + bytes([HEADER_VERSION])
            + _write_f64(float(self._header.get("created_at", time.time())))
            + wrapped
        )
        atomic_write_bytes(self.header_path, payload)

    # ==================================================================== #
    # Ключи
    # ==================================================================== #

    def _set_master_key(self, master_key: bytes) -> None:
        self._master_key = master_key
        self._k_manifest = subkey(master_key, info=_INFO_MANIFEST)
        self._k_blob_root = subkey(master_key, info=_INFO_BLOB_ROOT)

    @property
    def unlocked(self) -> bool:
        return self._master_key is not None

    def _wrapped(self) -> crypto.WrappedKey:
        return crypto.WrappedKey.from_bytes(self._header["wrapped"])

    def _require_unlocked(self) -> None:
        if self._master_key is None or self._k_manifest is None:
            raise VaultCorrupted("Хранилище заблокировано — сначала введите пароль")

    def _blob_key(self, blob_id: str) -> bytes:
        assert self._k_blob_root is not None
        return blobmod.blob_key(self._k_blob_root, blob_id)

    def forget_keys(self) -> None:
        """Вытираем ключи из памяти (при блокировке и выходе)."""
        self._master_key = None
        self._k_manifest = None
        self._k_blob_root = None
        self._manifest = None

    # ==================================================================== #
    # Разблокировка
    # ==================================================================== #

    def unlock(self, password: str, load_manifest: bool = True) -> UnlockOutcome:
        """Проверяет пароль.

        Пароль верный → расшифровываем манифест и возвращаем список папок.
        Пароль неверный → БЕЗ сообщения об ошибке возвращаем режим обманки.
        """
        master = unwrap_master_key(self._wrapped(), password)
        if master is None:
            return UnlockOutcome(
                authenticated=False,
                decoy_mode=True,
                decoy_folders=self.decoy_view.load(),
                message="ok",
            )
        self._set_master_key(master)
        if load_manifest:
            self._manifest = self._load_manifest()
        return UnlockOutcome(
            authenticated=True,
            folders=list(self._manifest.folders) if self._manifest else [],
            message="ok",
        )

    def _load_manifest(self) -> Manifest:
        self._require_unlocked()
        if not self.manifest_path.exists():
            return Manifest(created_at=time.time(), updated_at=time.time())
        raw = open_sealed(self.manifest_path.read_bytes(), self._k_manifest, aad=_INFO_MANIFEST)
        if raw is None:
            # откатываемся на самую свежую подходящую резервную копию
            for backup in reversed(self._manifest_backups()):
                data = open_sealed(backup.read_bytes(), self._k_manifest, aad=_INFO_MANIFEST)
                if data is not None:
                    return Manifest.from_json(data)
            raise VaultCorrupted("Манифест повреждён, резервные копии не подошли")
        return Manifest.from_json(raw)

    def _save_manifest(self) -> None:
        self._require_unlocked()
        if self._manifest is None:
            return
        self._manifest.updated_at = time.time()
        self._backup_manifest()
        atomic_write_bytes(self.manifest_path, seal(self._manifest.to_json(), self._k_manifest, aad=_INFO_MANIFEST))

    def _backup_manifest(self) -> None:
        if not self.manifest_path.exists():
            return
        ensure_dir(self.backups_dir)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.backups_dir / f"manifest-{stamp}-{int(time.time() * 1000) % 1000:03d}.enc"
        try:
            shutil.copy2(self.manifest_path, target)
        except OSError:
            return
        backups = self._manifest_backups()
        for old in backups[:-MAX_MANIFEST_BACKUPS]:
            robust_unlink(old)

    def _manifest_backups(self) -> list[Path]:
        if not self.backups_dir.exists():
            return []
        # сортируем и по времени, и по имени (в имени есть миллисекунды) — детерминированно
        return sorted(
            self.backups_dir.glob("manifest-*.enc"),
            key=lambda p: (p.stat().st_mtime, p.name),
        )

    @property
    def manifest(self) -> Manifest:
        self._require_unlocked()
        assert self._manifest is not None
        return self._manifest

    # ==================================================================== #
    # Состояние сессии (для «убили через диспетчер → всё вернулось»)
    # ==================================================================== #

    def read_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def write_state(self, **changes) -> dict:
        state = self.read_state()
        state.update(changes)
        state["updated_at"] = time.time()
        atomic_write_bytes(self.state_path, json.dumps(state, ensure_ascii=False, indent=1).encode("utf-8"))
        return state

    def begin_session(self) -> dict:
        """Помечает запуск. Если прошлый сеанс оборвался — вернём признак аварии."""
        state = self.read_state()
        crashed = state.get("clean_exit") is False
        self.write_state(clean_exit=False, session_started=time.time(), pid=os.getpid())
        state["crashed"] = crashed
        return state

    def end_session(self) -> None:
        self.write_state(clean_exit=True, session_ended=time.time())

    # ==================================================================== #
    # Журнал операций
    # ==================================================================== #

    def _journal(self, **data) -> None:
        data.setdefault("at", time.time())
        atomic_write_bytes(self.journal_path, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _clear_journal(self) -> None:
        robust_unlink(self.journal_path)

    def interrupted_operation(self) -> dict | None:
        """Есть ли незавершённая операция с прошлого запуска."""
        if not self.journal_path.exists():
            return None
        try:
            data = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(data, dict) and data.get("phase") not in (None, "done"):
            return data
        return None

    # ==================================================================== #
    # Планирование и проверка папок
    # ==================================================================== #

    def validate_folder(self, path: str | os.PathLike) -> list[str]:
        problems: list[str] = []
        p = Path(path)
        if not p.exists():
            return ["папка не найдена"]
        if not p.is_dir():
            return ["это файл, а не папка"]
        norm = normalize(p)
        if is_subpath(self.root, p) or same_path(p, self.root):
            return ["нельзя защищать папку самого хранилища"]
        if is_subpath(p, self.root):
            return ["папка внутри хранилища"]
        variants = {norm, norm.replace("/", "\\"), norm.replace("\\", "/")}
        for bad in FORBIDDEN_SUFFIXES:
            bad_win, bad_posix = bad.replace("/", "\\"), bad.replace("\\", "/")
            for v in variants:
                if v.endswith(bad_win) or v.endswith(bad_posix):
                    return [f"системный каталог защищать нельзя: {bad.strip(chr(92) + '/')}"]
                if (bad_win + "\\") in v or (bad_posix + "/") in v:
                    return [f"системный каталог защищать нельзя: {bad.strip(chr(92) + '/')}"]
        drive, tail = os.path.splitdrive(norm)
        if tail.strip("\\/") == "":
            return ["нельзя защищать корень диска"]
        home = normalize(Path.home())
        if same_path(norm, home):
            return ["нельзя защищать всю папку пользователя"]
        if self._manifest is not None:
            for rec in self._manifest.folders:
                if same_path(rec.path, p):
                    return ["эта папка уже защищена"]
                if is_subpath(p, rec.path):
                    return [f"папка уже внутри защищённой: {display_path(rec.path)}"]
                if is_subpath(rec.path, p):
                    return [f"внутри есть уже защищённая папка: {display_path(rec.path)}"]
        return problems

    def plan_folder(self, path: str | os.PathLike) -> FolderPlan:
        """Предпросмотр: сколько файлов и какой объём, есть ли проблемы."""
        problems = self.validate_folder(path)
        if problems:
            return FolderPlan(str(path), 0, 0, problems=problems)
        walk = walk_tree(path)
        need = int(walk.total_size * 1.02) + 64 * 1024 * 1024
        if free_space(self.root) < need:
            problems.append(
                f"мало места на диске хранилища: нужно ~{human_size(need)}, "
                f"свободно {human_size(free_space(self.root))}"
            )
        return FolderPlan(
            str(path),
            files=len(walk.files),
            size=walk.total_size,
            problems=problems,
            skipped=walk.skipped,
        )

    # ==================================================================== #
    # Добавление / удаление папок
    # ==================================================================== #

    def add_folder(
        self,
        path: str | os.PathLike,
        progress: Progress = _noop,
        *,
        purge_traces: bool = True,
        verify_deep: bool = True,
        wipe_passes: int = 1,
        keep_folder_visible: bool = True,
    ) -> FolderRecord:
        """Берёт папку под защиту: шифрует, проверяет, стирает оригиналы, кладёт приманки."""
        self._require_unlocked()
        assert self._manifest is not None
        target = Path(path)
        problems = self.validate_folder(target)
        if problems:
            raise FolderError("; ".join(problems))

        rec = FolderRecord(
            id=crypto.short_id(9),
            path=str(target),
            added_at=time.time(),
            locked=False,
        )
        self._manifest.folders.append(rec)
        self._ingest(
            rec,
            progress=progress,
            verify_deep=verify_deep,
            wipe_passes=wipe_passes,
            keep_folder_visible=keep_folder_visible,
        )
        self._save_manifest()
        self._update_decoy_view()
        if purge_traces:
            self.purge_traces()
        return rec

    def forget_folder(
        self,
        folder_id: str,
        progress: Progress = _noop,
        *,
        restore_first: bool = True,
        delete_blobs: bool = True,
    ) -> None:
        """Убирает папку из-под защиты (файлы остаются на диске открытыми)."""
        self._require_unlocked()
        assert self._manifest is not None
        rec = self._manifest.find(folder_id)
        if rec is None:
            raise FolderError("папка не найдена в хранилище")
        if restore_first and rec.locked:
            self.unlock_folder(folder_id, progress=progress)
        self._manifest.folders = [f for f in self._manifest.folders if f.id != folder_id]
        if delete_blobs:
            for blob_id in rec.blob_ids():
                robust_unlink(self.blobs_dir / f"{blob_id}.vkb")
        self.decoys.clear(rec.path)
        self._save_manifest()
        self._update_decoy_view()

    # ==================================================================== #
    # Блокировка / разблокировка
    # ==================================================================== #

    def lock_all(
        self,
        progress: Progress = _noop,
        *,
        purge_traces: bool = True,
        verify_deep: bool = True,
        wipe_passes: int = 1,
        keep_folder_visible: bool = True,
    ) -> int:
        self._require_unlocked()
        todo = [f for f in list(self.manifest.folders) if not f.locked]
        total_files = sum(f.file_count for f in todo) or 1
        done = 0
        for rec in todo:
            sub: Progress = lambda d, t, m, _done=done: progress(_done + d, total_files, m)  # noqa: E731
            self._ingest(
                rec,
                progress=sub,
                verify_deep=verify_deep,
                wipe_passes=wipe_passes,
                keep_folder_visible=keep_folder_visible,
            )
            done += max(rec.file_count, 1)
            self._save_manifest()
        if purge_traces and todo:
            self.purge_traces()
        self.write_state(phase="locked", locked_at=time.time())
        return len(todo)

    def unlock_all(self, progress: Progress = _noop, *, verify: bool = True) -> int:
        self._require_unlocked()
        todo = list(self.manifest.locked_folders)
        total_files = sum(f.file_count for f in todo) or 1
        done = 0
        for rec in todo:
            sub: Progress = lambda d, t, m, _done=done: progress(_done + d, total_files, m)  # noqa: E731
            self._extract(rec, progress=sub, verify=verify)
            done += max(rec.file_count, 1)
            self._save_manifest()
        self.write_state(phase="unlocked", unlocked_at=time.time())
        return len(todo)

    def lock_folder(self, folder_id: str, progress: Progress = _noop, **kwargs) -> None:
        rec = self.manifest.find(folder_id)
        if rec is None:
            raise FolderError("папка не найдена")
        if rec.locked:
            return
        self._ingest(rec, progress=progress, **kwargs)
        self._save_manifest()
        self._update_decoy_view()

    def ingest_changes(self, folder_id: str, progress: Progress = _noop, **kwargs) -> int:
        """Дошифровать новые/изменённые файлы уже защищённой папки.

        Используется наблюдателем: пользователь бросил файл в папку —
        его нужно спрятать немедленно, не дожидаясь ручной блокировки.
        Возвращает число файлов, которые были зашифрованы в этот проход.
        """
        rec = self.manifest.find(folder_id)
        if rec is None:
            raise FolderError("папка не найдена")
        root = Path(rec.path)
        if not root.exists():
            return 0
        known = {f.rel for f in rec.files}
        walk = walk_tree(root)
        fresh = [f for f in walk.files if f.rel not in known]
        if not fresh and walk.ok:
            return 0
        if not walk.ok:
            # что-то не читается — не трогаем, наблюдатель попробует позже
            return 0
        before = rec.file_count
        self._ingest(rec, progress=progress, **kwargs)
        self._save_manifest()
        self._update_decoy_view()
        return rec.file_count - before

    def unlock_folder(self, folder_id: str, progress: Progress = _noop, *, verify: bool = True) -> None:
        rec = self.manifest.find(folder_id)
        if rec is None:
            raise FolderError("папка не найдена")
        if not rec.locked:
            return
        self._extract(rec, progress=progress, verify=verify)
        self._save_manifest()

    # ==================================================================== #
    # Внутренние: упаковка и распаковка
    # ==================================================================== #

    def _ingest(
        self,
        rec: FolderRecord,
        progress: Progress = _noop,
        *,
        verify_deep: bool = True,
        wipe_passes: int = 1,
        purge_traces: bool = False,
        keep_folder_visible: bool = True,
    ) -> None:
        """Папка → блобы. Оригиналы стираются только после полной проверки.

        keep_folder_visible=False — папка убирается с диска целиком:
        ни проводник, ни диалог «добавить файл» в браузере её не увидят.
        """
        ensure_dir(self.blobs_dir)
        root = Path(rec.path)
        if not root.exists():
            raise FolderError(f"папка исчезла: {rec.path}")

        self._journal(op="ingest", folder_id=rec.id, phase="scan", path=rec.path)
        walk = walk_tree(root)
        if not walk.ok:
            reasons = "; ".join(f"{rel} — {why}" for rel, why in walk.skipped[:5])
            raise FolderError(f"не удалось прочитать часть файлов, операция отменена: {reasons}")

        need = int(walk.total_size * 1.02) + 32 * 1024 * 1024
        if free_space(self.root) < need:
            raise FolderError(
                f"недостаточно места: нужно ~{human_size(need)}, свободно {human_size(free_space(self.root))}"
            )

        previous = {f.rel: f for f in rec.files}
        entries: list[FileEntry] = []
        total = max(len(walk.files), 1)
        self._journal(op="ingest", folder_id=rec.id, phase="encrypt", path=rec.path, files_total=total)

        for index, item in enumerate(walk.files):
            progress(index, total, item.rel)
            reused = self._reusable_blob(previous.get(item.rel), item.size, item.mtime_ns)
            if reused is not None:
                entries.append(
                    FileEntry(
                        rel=item.rel,
                        blob=reused.blob,
                        size=item.size,
                        sha256=reused.sha256,
                        mtime_ns=item.mtime_ns,
                        attrs=item.attrs,
                    )
                )
                continue

            blob_id = crypto.short_id(16)
            dst = self.blobs_dir / f"{blob_id}.vkb"
            try:
                info = blobmod.encrypt_file(item.path, dst, self._blob_key(blob_id))
            except OSError as exc:
                raise FolderError(f"не удалось зашифровать {item.rel}: {exc}") from exc
            if info.size != item.size:
                robust_unlink(dst)
                raise IntegrityError(f"{item.rel}: размер после шифрования не совпал")

            # проверка: заголовок всегда, полное чтение — по настройке
            hdr_size, hdr_sha, _, _ = blobmod.read_header(dst, self._blob_key(blob_id))
            if hdr_size != item.size or hdr_sha != info.sha256:
                robust_unlink(dst)
                raise IntegrityError(f"{item.rel}: заголовок блоба не совпал с исходным файлом")
            if verify_deep:
                probe = self.root / "verify.tmp"
                try:
                    blobmod.decrypt_file(dst, probe, self._blob_key(blob_id), verify=True)
                finally:
                    robust_unlink(probe)

            entries.append(
                FileEntry(
                    rel=item.rel,
                    blob=blob_id,
                    size=info.size,
                    sha256=info.sha256,
                    mtime_ns=item.mtime_ns,
                    attrs=item.attrs,
                )
            )

        progress(total, total, "проверка завершена")
        self._journal(
            op="ingest",
            folder_id=rec.id,
            phase="verified",
            path=rec.path,
            files_total=total,
            blobs=len(entries),
        )

        # ---- точка невозврата: данные проверены, стираем оригиналы ---- #
        old_blobs = {f.blob for f in rec.files}
        new_blobs = {f.blob for f in entries}
        stale = old_blobs - new_blobs

        removed = secure_delete_tree(root, passes=wipe_passes, keep_root=keep_folder_visible)
        rec.files = entries
        rec.dirs = [DirEntry(rel=d.rel, mtime_ns=d.mtime_ns, attrs=d.attrs) for d in walk.dirs]
        rec.locked = True

        for blob_id in stale:
            robust_unlink(self.blobs_dir / f"{blob_id}.vkb")

        if keep_folder_visible:
            self.decoys.materialize(root)
        set_hidden_system(self.blobs_dir, True)
        self._journal(op="ingest", folder_id=rec.id, phase="done", path=rec.path, wiped=removed)
        self._clear_journal()
        self.write_state(phase="locked", locked_at=time.time())
        if purge_traces:
            self.purge_traces()

    def _reusable_blob(self, prev: FileEntry | None, size: int, mtime_ns: int) -> FileEntry | None:
        """Можно ли не перешифровывать файл (размер и время изменения не поменялись)."""
        if prev is None:
            return None
        if prev.size != size or prev.mtime_ns != mtime_ns:
            return None
        path = self.blobs_dir / f"{prev.blob}.vkb"
        if not path.exists():
            return None
        try:
            hdr_size, hdr_sha, _, _ = blobmod.read_header(path, self._blob_key(prev.blob))
        except (VaultCorrupted, OSError):
            return None
        if hdr_size != prev.size or hdr_sha != prev.sha256:
            return None
        return prev

    def _extract(self, rec: FolderRecord, progress: Progress = _noop, *, verify: bool = True) -> None:
        """Блобы → папка. Сначала убираем приманки, потом пишем файлы атомарно."""
        root = Path(rec.path)
        ensure_dir(root)
        self.decoys.clear(root)

        total = max(len(rec.files), 1)
        self._journal(op="extract", folder_id=rec.id, phase="write", path=rec.path, files_total=total)

        # каталоги — до файлов
        for d in rec.dirs:
            ensure_dir(root / rel_to_os(d.rel))

        for index, entry in enumerate(rec.files):
            progress(index, total, entry.rel)
            src = self.blobs_dir / f"{entry.blob}.vkb"
            if not src.exists():
                raise IntegrityError(f"блоб не найден для {entry.rel} — восстановление прервано")
            dst = root / rel_to_os(entry.rel)
            ensure_dir(dst.parent)
            tmp = dst.with_name(dst.name + ".vkpart")
            try:
                blobmod.decrypt_file(src, tmp, self._blob_key(entry.blob), verify=verify)
                robust_replace(tmp, dst)
            finally:
                robust_unlink(tmp)
            touch_mtime(dst, entry.mtime_ns)

        for d in rec.dirs:
            touch_mtime(root / rel_to_os(d.rel), d.mtime_ns)

        rec.locked = False
        self._journal(op="extract", folder_id=rec.id, phase="done", path=rec.path)
        self._clear_journal()
        self.write_state(phase="unlocked", unlocked_at=time.time())
        progress(total, total, "готово")

    # ==================================================================== #
    # Витрина обманок
    # ==================================================================== #

    def _update_decoy_view(self) -> None:
        """Сохраняет, что показывать при неверном пароле."""
        if self._manifest is None:
            return
        view: list[DecoyViewFolder] = []
        for rec in self._manifest.folders:
            view.append(
                DecoyViewFolder(
                    path=rec.path,
                    name=rec.name,
                    files=rec.file_count,
                    size=rec.total_size,
                    note=rec.note,
                    folder_id=rec.id,
                )
            )
        self.decoy_view.save(view)

    def materialize_decoys_for_view(self) -> int:
        """Сеанс обманки: создаёт папки и раскладывает в них приманки.

        Нужен, когда при блокировке папки убираются полностью: без пароля
        их не видно нигде, а на время «неверного» сеанса они появляются.
        """
        count = 0
        for d in self.decoy_view.load():
            try:
                ensure_dir(d.path)
                self.decoys.materialize(d.path)
                count += 1
            except OSError:
                continue
        return count

    def clear_decoy_folders(self, remove_folders: bool = True) -> int:
        """Убирает приманки после сеанса обманки; по желанию — и сами папки."""
        removed = 0
        for d in self.decoy_view.load():
            root = Path(d.path)
            try:
                self.decoys.clear(root)
            except OSError:
                continue
            if remove_folders and root.is_dir():
                try:
                    if not any(root.iterdir()):
                        root.rmdir()
                        removed += 1
                except OSError:
                    pass
        return removed

    # ==================================================================== #
    # Скрытый буфер: спрятать файл немедленно, даже без ключа в памяти
    # ==================================================================== #

    @property
    def stage_dir(self) -> Path:
        return self.root / "stage"

    def _stage_meta(self) -> Path:
        return self.stage_dir / "index.json"

    def _load_stage(self) -> list[dict]:
        p = self._stage_meta()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save_stage(self, items: list[dict]) -> None:
        ensure_dir(self.stage_dir)
        atomic_write_bytes(self._stage_meta(), json.dumps(items, ensure_ascii=False).encode("utf-8"))
        set_hidden_system(self.stage_dir, True)

    def stage_new_files(self, folder_id: str, paths: Iterable[Path | str]) -> int:
        """Мгновенно убирает файлы из папки в скрытый буфер хранилища.

        Используется, когда мастер-ключ не держится в памяти: файл исчезает
        из папки сразу, а шифруется в блобы при следующем открытии сейфа.
        """
        items = self._load_stage()
        ensure_dir(self.stage_dir)
        moved = 0
        for src in paths:
            src = Path(src)
            if not src.is_file():
                continue
            staged = f"{crypto.short_id(8)}_{src.name}"
            dst = self.stage_dir / staged
            try:
                robust_replace(src, dst)
            except OSError:
                try:  # другой том: копируем и стираем оригинал
                    shutil.copy2(src, dst)
                    secure_delete_file(src)
                except OSError:
                    continue
            items.append(
                {
                    "folder_id": folder_id,
                    "rel": src.name,
                    "staged": staged,
                    "at": time.time(),
                }
            )
            moved += 1
        if moved:
            self._save_stage(items)
        return moved

    def ingest_staged(self, progress: Progress = _noop) -> int:
        """Зашифровать содержимое буфера в блобы (сразу после разблокировки)."""
        self._require_unlocked()
        items = self._load_stage()
        if not items:
            return 0
        total = len(items)
        done = 0
        for index, item in enumerate(items):
            progress(index, total, str(item.get("rel", "")))
            rec = self.manifest.find(str(item.get("folder_id", "")))
            src = self.stage_dir / str(item.get("staged", ""))
            if rec is None or not src.exists():
                continue
            blob_id = crypto.short_id(16)
            dst = self.blobs_dir / f"{blob_id}.vkb"
            info = blobmod.encrypt_file(src, dst, self._blob_key(blob_id))
            mtime = src.stat().st_mtime_ns
            rec.files.append(
                FileEntry(
                    rel=str(item.get("rel", src.name)),
                    blob=blob_id,
                    size=info.size,
                    sha256=info.sha256,
                    mtime_ns=mtime,
                )
            )
            robust_unlink(src)
            done += 1
        self._save_stage([])
        robust_unlink(self._stage_meta())
        try:
            self.stage_dir.rmdir()
        except OSError:
            pass
        self._save_manifest()
        self._update_decoy_view()
        progress(total, total, "готово")
        return done

    def staged_count(self) -> int:
        return len(self._load_stage())

    # ==================================================================== #
    # Следы
    # ==================================================================== #

    def protected_paths(self) -> list[str]:
        if self._manifest is None:
            return [f.path for f in self.decoy_view.load()]
        return [rec.path for rec in self._manifest.folders]

    def purge_traces(self, **kwargs) -> TraceReport:
        return self.traces.purge(self.protected_paths(), **kwargs)

    # ==================================================================== #
    # Пароль, восстановление, проверка
    # ==================================================================== #

    def change_password(self, old_password: str, new_password: str) -> None:
        """Переупаковывает master_key. Данные не перешифровываются — это быстро."""
        master = unwrap_master_key(self._wrapped(), old_password)
        if master is None:
            raise VaultCorrupted("Старый пароль не подошёл")
        if len(new_password) < 4:
            raise VaultCorrupted("Новый пароль слишком короткий")
        wrapped = wrap_master_key(master, new_password, iterations=max(self.iterations, DEFAULT_ITERATIONS))
        self._header["wrapped"] = wrapped.to_bytes()
        self._write_header()
        self._set_master_key(master)
        if self.emergency.available:
            try:
                self.emergency.save(master)
            except EmergencyUnavailable:
                pass

    def _save_recovery_code(self, master_key: bytes, recovery_code: str) -> None:
        salt = crypto.random_bytes(32)
        blob = wrap_with_recovery(master_key, recovery_code, salt)
        atomic_write_bytes(self.recovery_path, salt + blob)

    def rotate_recovery_code(self) -> str:
        """Новый код восстановления (старый перестаёт работать)."""
        self._require_unlocked()
        assert self._master_key is not None
        code = generate_recovery_code()
        self._save_recovery_code(self._master_key, code)
        return code

    def unlock_with_recovery_code(self, recovery_code: str) -> UnlockOutcome:
        """Вход по коду восстановления, если пароль забыт."""
        if not self.recovery_path.exists():
            raise EmergencyUnavailable("Код восстановления не задавался")
        raw = self.recovery_path.read_bytes()
        salt, blob = raw[:32], raw[32:]
        master = unwrap_with_recovery(blob, recovery_code, salt)
        if master is None:
            raise EmergencyUnavailable("Код восстановления не подошёл")
        self._set_master_key(master)
        self._manifest = self._load_manifest()
        return UnlockOutcome(authenticated=True, folders=list(self._manifest.folders), message="recovery-code")

    def emergency_unlock(self) -> UnlockOutcome:
        """Вход без пароля через ключ DPAPI (та же учётная запись Windows)."""
        master = self.emergency.load()  # может поднять EmergencyUnavailable
        self._set_master_key(master)
        self._manifest = self._load_manifest()
        return UnlockOutcome(authenticated=True, folders=list(self._manifest.folders), message="emergency")

    @property
    def emergency_available(self) -> bool:
        """Доступен ли вход без пароля (аварийный ключ DPAPI)."""
        if not self.emergency.available:
            return False
        return self.emergency.has_key()

    def restore_after_crash(self, progress: Progress = _noop) -> int:
        """«Убили через диспетчер → всё вернулось как было».

        Использует аварийный ключ DPAPI, поэтому пароль не нужен.
        """
        outcome = self.emergency_unlock()
        if not outcome.authenticated:  # pragma: no cover - защита
            raise EmergencyUnavailable("Аварийное восстановление не удалось")
        count = self.unlock_all(progress=progress)
        self.write_state(phase="unlocked", restored_after_crash=time.time())
        return count

    def verify_integrity(self, progress: Progress = _noop, *, deep: bool = False) -> list[str]:
        """Проверяет все блобы. Возвращает список найденных проблем."""
        self._require_unlocked()
        problems: list[str] = []
        entries = [(rec, e) for rec in self.manifest.folders for e in rec.files]
        total = max(len(entries), 1)
        probe = self.root / "verify.tmp"
        for index, (rec, entry) in enumerate(entries):
            progress(index, total, f"{rec.name}/{entry.rel}")
            src = self.blobs_dir / f"{entry.blob}.vkb"
            if not src.exists():
                problems.append(f"{rec.name}/{entry.rel}: блоб отсутствует")
                continue
            try:
                size, sha, _, _ = blobmod.read_header(src, self._blob_key(entry.blob))
            except VaultCorrupted as exc:
                problems.append(f"{rec.name}/{entry.rel}: {exc}")
                continue
            if size != entry.size or sha != entry.sha256:
                problems.append(f"{rec.name}/{entry.rel}: не совпадает размер/контрольная сумма")
                continue
            if deep:
                try:
                    blobmod.decrypt_file(src, probe, self._blob_key(entry.blob), verify=True)
                except (IntegrityError, VaultCorrupted) as exc:
                    problems.append(f"{rec.name}/{entry.rel}: {exc}")
                finally:
                    robust_unlink(probe)
        progress(total, total, "готово")
        return problems

    def prune_orphan_blobs(self) -> int:
        """Удаляет блобы, на которые никто не ссылается (после сбоев)."""
        self._require_unlocked()
        used = self.manifest.all_blob_ids()
        removed = 0
        if self.blobs_dir.exists():
            for f in self.blobs_dir.glob("*.vkb"):
                if f.stem not in used and robust_unlink(f):
                    removed += 1
            for f in self.blobs_dir.glob("*.part"):
                robust_unlink(f)
        return removed

    # ==================================================================== #
    # Сводка для UI
    # ==================================================================== #

    def stats(self) -> dict:
        locked = self.manifest.locked_folders if self._manifest else []
        unlocked = self.manifest.unlocked_folders if self._manifest else []
        blob_count = len(list(self.blobs_dir.glob("*.vkb"))) if self.blobs_dir.exists() else 0
        blob_size = (
            sum(f.stat().st_size for f in self.blobs_dir.glob("*.vkb")) if self.blobs_dir.exists() else 0
        )
        return {
            "folders_total": len(self._manifest.folders) if self._manifest else 0,
            "folders_locked": len(locked),
            "folders_unlocked": len(unlocked),
            "files": sum(f.file_count for f in self._manifest.folders) if self._manifest else 0,
            "size": sum(f.total_size for f in self._manifest.folders) if self._manifest else 0,
            "blobs": blob_count,
            "vault_size": blob_size,
            "created_at": float(self._header.get("created_at", 0.0)),
            "emergency": self.emergency_available,
            "decoys": self.decoys.count,
            "human_size": human_size(sum(f.total_size for f in self._manifest.folders) if self._manifest else 0),
            "human_vault": human_size(blob_size),
        }


# --------------------------------------------------------------------------- #
# Упаковка float в 8 байт без struct-зависимостей в заголовке
# --------------------------------------------------------------------------- #

def _write_f64(value: float) -> bytes:
    return struct.pack(">d", value)


def _read_f64(data: bytes) -> float:
    return struct.unpack(">d", data[:8])[0]

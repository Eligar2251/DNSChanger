"""Зачистка следов: «Недавние файлы», быстрый доступ, прыг-списки, эскизы, поиск.

Само по себе шифрование убирает данные, но Windows запоминает имена и пути:
в быстром доступе, в Recent, в эскизах проводника и в индексе поиска.
Здесь всё это вычищается.

Логика намеренно разделена на «чистые» функции поиска совпадений
(тестируются на любой ОС) и «боевые» действия в каталогах Windows.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .fsutil import IS_WINDOWS, normalize, robust_unlink, same_path
from .win import (
    clear_recent_docs_subkey,
    is_admin,
    notify_shell,
    read_recent_docs_entries,
    restart_explorer,
    set_quick_access_recent,
)

#: ID прыг-списка «Быстрый доступ» (закреплённые и частые папки).
QUICK_ACCESS_DESTINATION = "f01b4d95cf55d32a.automaticDestinations-ms"


@dataclass
class TraceReport:
    """Что удалось почистить — показывается пользователю в настройках."""

    shortcuts_removed: int = 0
    jumplists_removed: int = 0
    thumbs_removed: int = 0
    mru_cleared: int = 0
    search_index: str = ""
    warnings: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.shortcuts_removed + self.jumplists_removed + self.thumbs_removed + self.mru_cleared

    def summary(self) -> str:
        parts = []
        if self.shortcuts_removed:
            parts.append(f"ярлыков: {self.shortcuts_removed}")
        if self.jumplists_removed:
            parts.append(f"списков быстрого доступа: {self.jumplists_removed}")
        if self.thumbs_removed:
            parts.append(f"кэшей эскизов: {self.thumbs_removed}")
        if self.mru_cleared:
            parts.append(f"записей MRU: {self.mru_cleared}")
        if self.search_index:
            parts.append(f"индекс поиска: {self.search_index}")
        return ", ".join(parts) if parts else "следов не найдено"


# --------------------------------------------------------------------------- #
# Чистая логика поиска (тестируется везде)
# --------------------------------------------------------------------------- #


def path_needles(path: str) -> list[bytes]:
    """Все байтовые представления пути, которые может содержать .lnk или прыг-список.

    Windows хранит пути и в Unicode (UTF-16LE), и в локальной кодовой странице,
    поэтому ищем оба варианта — и сам путь, и его родительские каталоги.
    """
    p = os.fspath(path).strip()
    if not p:
        return []
    variants: list[str] = [p, p.rstrip("\\/")]
    norm = normalize(p)
    variants.append(norm)
    # родительские каталоги: ярлык на файл внутри защищаемой папки тоже выдаёт её
    parent = Path(variants[0]).parent
    for _ in range(3):
        s = str(parent)
        if s and s not in variants:
            variants.append(s)
        if parent == parent.parent:
            break
        parent = parent.parent

    needles: list[bytes] = []
    for v in variants:
        v = v.strip()
        if len(v) < 3:
            continue
        for encoding in ("utf-16-le", "utf-8", "mbcs", "cp1251"):
            try:
                raw = v.encode(encoding, "ignore")
            except (LookupError, UnicodeError):
                continue
            if len(raw) >= 6 and raw not in needles:
                needles.append(raw)
    return needles


def data_references(data: bytes, needles: list[bytes]) -> bool:
    """Содержит ли бинарник (.lnk, .automaticDestinations-ms) хотя бы одну иглу."""
    return any(n in data for n in needles)


def file_references(path: Path | str, needles: list[bytes], max_bytes: int = 8 << 20) -> bool:
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes)
    except OSError:
        return False
    return data_references(data, needles)


def name_matches(name: str, protected_names: set[str]) -> bool:
    """Совпадает ли имя из MRU с именем защищённого файла/папки."""
    return name.strip().lower() in protected_names


# --------------------------------------------------------------------------- #
# Боевая зачистка
# --------------------------------------------------------------------------- #


class TraceCleaner:
    def __init__(self, appdata: Path | str | None = None, local_appdata: Path | str | None = None):
        self.appdata = Path(appdata or os.environ.get("APPDATA") or "~/.config")
        self.local_appdata = Path(local_appdata or os.environ.get("LOCALAPPDATA") or "~/.cache")

    # -- каталоги --------------------------------------------------------- #
    @property
    def recent_dir(self) -> Path:
        return self.appdata / "Microsoft" / "Windows" / "Recent"

    @property
    def jump_dirs(self) -> list[Path]:
        return [self.recent_dir / "AutomaticDestinations", self.recent_dir / "CustomDestinations"]

    @property
    def thumb_dir(self) -> Path:
        return self.local_appdata / "Microsoft" / "Windows" / "Explorer"

    # -- действия --------------------------------------------------------- #
    def clean_shortcuts(self, protected_paths: list[str]) -> tuple[int, list[str]]:
        """Удаляет ярлыки «Недавних», ведущие внутрь защищённых папок."""
        needles = [n for p in protected_paths for n in path_needles(p)]
        if not needles or not self.recent_dir.exists():
            return 0, []
        removed, failed = 0, []
        for lnk in list(self.recent_dir.glob("*.lnk")):
            if file_references(lnk, needles):
                if robust_unlink(lnk):
                    removed += 1
                else:
                    failed.append(lnk.name)
        return removed, failed

    def clean_jumplists(self, protected_paths: list[str]) -> tuple[int, list[str]]:
        """Чистит прыг-списки (они же — закреплённые и частые папки быстрого доступа)."""
        needles = [n for p in protected_paths for n in path_needles(p)]
        removed, failed = 0, []
        for directory in self.jump_dirs:
            if not directory.exists():
                continue
            for f in list(directory.iterdir()):
                if not f.is_file():
                    continue
                hit = f.name == QUICK_ACCESS_DESTINATION or file_references(f, needles)
                if not hit:
                    continue
                if robust_unlink(f):
                    removed += 1
                else:
                    failed.append(f.name)
        return removed, failed

    def clean_thumbcache(self) -> tuple[int, bool]:
        """Удаляет кэши эскизов. Возвращает (число, нужен ли перезапуск проводника)."""
        if not self.thumb_dir.exists():
            return 0, False
        removed = 0
        blocked = False
        for pattern in ("thumbcache_*.db", "iconcache_*.db"):
            for f in list(self.thumb_dir.glob(pattern)):
                if robust_unlink(f):
                    removed += 1
                else:
                    blocked = True
        return removed, blocked

    def clean_mru(self, protected_paths: list[str]) -> int:
        """Убирает имена защищённых файлов из реестрового MRU «Недавние документы»."""
        if not IS_WINDOWS:  # pragma: no cover
            return 0
        names: set[str] = set()
        for p in protected_paths:
            names.add(Path(p).name.lower())
            try:
                for child in Path(p).rglob("*"):
                    names.add(child.name.lower())
            except OSError:
                continue
        if not names:
            return 0
        cleared = 0
        seen: set[str] = set()
        for subkey, entry in read_recent_docs_entries():
            if subkey in seen:
                continue
            if name_matches(entry, names) or name_matches(Path(entry).name, names):
                if clear_recent_docs_subkey(subkey):
                    cleared += 1
                    seen.add(subkey)
        return cleared

    def rebuild_search_index(self) -> str:
        """Полный сброс индекса поиска Windows (нужны права администратора)."""
        if not IS_WINDOWS:  # pragma: no cover
            return "недоступно вне Windows"
        if not is_admin():
            return "нужны права администратора"
        edb = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / (
            "Microsoft/Search/Data/Applications/Windows/Windows.edb"
        )
        try:  # pragma: no cover
            subprocess.run(["net", "stop", "WSearch"], check=False, capture_output=True)
            ok = robust_unlink(edb) if edb.exists() else True
            subprocess.run(["net", "start", "WSearch"], check=False, capture_output=True)
            return "перестроен" if ok else "файл индекса заблокирован"
        except OSError as exc:
            return f"ошибка: {exc}"

    def disable_quick_access_lists(self, enable: bool = False) -> bool:
        """Полностью выключает «Недавние файлы» и «Частые папки» в проводнике."""
        return set_quick_access_recent(show_recent=enable, show_frequent=enable)

    def purge(
        self,
        protected_paths: list[str],
        *,
        clean_thumbs: bool = True,
        clean_mru: bool = True,
        quick_access_off: bool = True,
        rebuild_index: bool = False,
    ) -> TraceReport:
        report = TraceReport()
        if not protected_paths and not clean_thumbs:
            return report

        report.shortcuts_removed, failed = self.clean_shortcuts(protected_paths)
        report.warnings.extend(f"не удалён ярлык {n}" for n in failed)

        report.jumplists_removed, failed = self.clean_jumplists(protected_paths)
        report.warnings.extend(f"не удалён список {n}" for n in failed)

        if quick_access_off:
            if self.disable_quick_access_lists(False):
                report.details.append("«Недавние» и «Частые папки» в быстром доступе отключены")
            else:
                report.warnings.append("не удалось отключить списки быстрого доступа")

        if clean_thumbs:
            report.thumbs_removed, blocked = self.clean_thumbcache()
            if blocked:
                report.warnings.append("кэш эскизов занят проводником — нужен его перезапуск")

        if clean_mru:
            report.mru_cleared = self.clean_mru(protected_paths)

        if rebuild_index:
            report.search_index = self.rebuild_search_index()

        notify_shell()
        return report


def purge_for_paths(protected_paths: list[str], **kwargs) -> TraceReport:
    return TraceCleaner().purge([str(p) for p in protected_paths], **kwargs)


def restart_explorer_now() -> bool:
    """Пользователь нажал «Перезапустить проводник» — чистим кэш эскизов после."""
    if not IS_WINDOWS:  # pragma: no cover
        return False
    ok = restart_explorer()
    if ok:
        TraceCleaner().clean_thumbcache()
        notify_shell()
    return ok


def protected_names_of(paths: list[str]) -> set[str]:
    out = set()
    for p in paths:
        out.add(Path(p).name.lower())
    return out


def is_inside_protected(path: str, protected: list[str]) -> bool:
    norm = normalize(path)
    return any(same_path(norm, pr) or norm.startswith(normalize(pr) + os.sep) for pr in protected)


if sys.platform != "win32":  # pragma: no cover - подсказка для линтера
    _ = QUICK_ACCESS_DESTINATION

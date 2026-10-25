"""Папки-обманки и файлы-приманки.

Идея: при неверном пароле приложение ведёт себя так, будто пароль верный, —
а в папках лежат приманки. Нападающий видит «свои» папки, пару безобидных
файлов и успокаивается. Настоящих данных в этот момент на диске нет:
они лежат в AES-256-GCM контейнере.

Приманки НЕ шифруются — они и должны быть видны. Но важно другое:
при разблокировке мы удаляем только те файлы, которые сами положили
(сверяем sha256), поэтому приманки никогда не перепутываются с
пользовательскими файлами.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .crypto import open_sealed, seal, subkey
from .fsutil import (
    atomic_write_bytes,
    clear_readonly,
    display_path,
    ensure_dir,
    human_size,
    is_reparse_point,
    normalize,
    robust_unlink,
    same_path,
)

#: Ключ «обёртки» списка защищаемых папок для режима обманки.
#: Секретом не является: сами папки и так видны в проводнике (они пустые/с приманками).
_VIEW_INFO = b"vk-decoy-view/v1"
_APP_SEED = b"VaultKeeper.app.v1"

DEFAULT_DECOYS: tuple[tuple[str, str], ...] = (
    ("Заметки.txt", "Список дел на неделю:\n1. Оплатить интернет\n2. Записаться к врачу\n3. Купить подарок маме\n"),
    ("Пароли (черновик).txt", "wifi: dom2019\nпочта: изменить пароль!\nкарта: **** **** **** 4213\n"),
    ("Рецепт борща.txt", "Свёкла, капуста, картофель, морковь, лук, томатная паста, бульон.\nВарить 40 минут.\n"),
)


@dataclass
class DecoyItem:
    name: str
    size: int
    is_dir: bool
    added_at: float

    @property
    def human_size(self) -> str:
        return human_size(self.size)


@dataclass
class PlacedFile:
    """Файл приманки, который мы положили в конкретную папку."""

    rel: str
    sha256: str
    size: int


@dataclass
class PlacedRecord:
    target: str
    files: list[PlacedFile] = field(default_factory=list)
    placed_at: float = 0.0


class DecoyStore:
    """Хранилище приманок + учёт, куда они были положены."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.store = self.root / "store"
        self.placed_path = self.root / "placed.json"
        ensure_dir(self.store)

    # -- содержимое склада ------------------------------------------------ #
    def items(self) -> list[DecoyItem]:
        out: list[DecoyItem] = []
        for child in sorted(self.store.iterdir()):
            if child.name.endswith(".vkpart"):
                continue
            try:
                st = child.stat()
            except OSError:
                continue
            if child.is_dir():
                size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
                out.append(DecoyItem(child.name, size, True, st.st_mtime))
            else:
                out.append(DecoyItem(child.name, st.st_size, False, st.st_mtime))
        return out

    @property
    def count(self) -> int:
        return len(self.items())

    @property
    def total_size(self) -> int:
        return sum(i.size for i in self.items())

    def seed_defaults(self, force: bool = False) -> int:
        """Создаёт набор приманок по умолчанию, если склад пуст."""
        if not force and any(self.store.iterdir()):
            return 0
        created = 0
        for name, text in DEFAULT_DECOYS:
            target = self.store / name
            if target.exists():
                continue
            target.write_text(text, encoding="utf-8")
            created += 1
        return created

    def add(self, source: Path | str) -> str | None:
        """Кладёт файл или папку на склад приманок. Возвращает имя или ``None``."""
        src = Path(source)
        if not src.exists() or is_reparse_point(src):
            return None
        name = self._unique_name(src.name)
        dst = self.store / name
        try:
            if src.is_dir():
                shutil.copytree(src, dst, symlinks=False, ignore_dangling_symlinks=True)
            else:
                shutil.copy2(src, dst)
        except OSError:
            return None
        return name

    def remove(self, name: str) -> bool:
        target = self.store / Path(name).name
        if not target.exists():
            return False
        clear_readonly(target)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            return not target.exists()
        return robust_unlink(target)

    def clear_all(self) -> None:
        if self.store.exists():
            shutil.rmtree(self.store, ignore_errors=True)
        ensure_dir(self.store)

    def _unique_name(self, name: str) -> str:
        candidate = Path(name).name or "file"
        if not (self.store / candidate).exists():
            return candidate
        stem, suffix = Path(candidate).stem, Path(candidate).suffix
        for i in range(2, 1000):
            candidate = f"{stem} ({i}){suffix}"
            if not (self.store / candidate).exists():
                return candidate
        return f"{stem}-{int(time.time())}{suffix}"

    # -- раскладка по папкам --------------------------------------------- #
    def materialize(self, target_dir: Path | str) -> PlacedRecord:
        """Кладёт приманки в папку. Существующие чужие файлы не трогает."""
        target = Path(target_dir)
        ensure_dir(target)
        record = PlacedRecord(target=str(target), placed_at=time.time())

        for item in sorted(self.store.iterdir()):
            dst = target / item.name
            if dst.exists():
                # если это наша же приманка — пропускаем, если чужой файл — тоже не трогаем
                continue
            try:
                if item.is_dir():
                    shutil.copytree(item, dst, symlinks=False)
                else:
                    shutil.copy2(item, dst)
            except OSError:
                continue
            if dst.is_file():
                record.files.append(
                    PlacedFile(rel=item.name, sha256=_sha256(dst), size=dst.stat().st_size)
                )
            else:
                for sub in sorted(dst.rglob("*")):
                    if sub.is_file():
                        record.files.append(
                            PlacedFile(
                                rel=str(sub.relative_to(target)).replace("\\", "/"),
                                sha256=_sha256(sub),
                                size=sub.stat().st_size,
                            )
                        )
        self._save_placed(record)
        return record

    def clear(self, target_dir: Path | str) -> int:
        """Убирает приманки из папки — и только их (сверяем sha256)."""
        target = normalize(target_dir)
        records = self._load_placed()
        removed = 0
        keep: list[dict] = []
        for rec in records:
            if not same_path(rec["target"], target):
                keep.append(rec)
                continue
            for f in rec.get("files", []):
                p = Path(target_dir).joinpath(*str(f["rel"]).split("/"))
                if not p.is_file():
                    continue
                if _sha256(p) != f.get("sha256"):
                    continue  # файл изменили — значит это уже не наша приманка
                clear_readonly(p)
                if robust_unlink(p):
                    removed += 1
            # подчистим пустые каталоги, которые создали мы
            dirs = {Path(*f["rel"].split("/")[:-1]) for f in rec.get("files", []) if "/" in f["rel"]}
            for d in sorted(dirs, key=lambda x: -len(x.parts)):
                if str(d):
                    candidate = Path(target_dir) / d
                    try:
                        candidate.rmdir()
                    except OSError:
                        pass
        self.placed_path.parent.mkdir(parents=True, exist_ok=True)
        self.placed_path.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
        return removed

    def clear_everywhere(self) -> int:
        total = 0
        for rec in self._load_placed():
            total += self.clear(rec["target"])
        return total

    def placed_targets(self) -> list[str]:
        return [r["target"] for r in self._load_placed()]

    def placed_rels(self, target_dir: Path | str) -> set[str]:
        """Относительные пути приманок, которые мы положили в эту папку."""
        target = normalize(target_dir)
        out: set[str] = set()
        for rec in self._load_placed():
            if same_path(rec.get("target", ""), target):
                for f in rec.get("files", []):
                    out.add(str(f.get("rel", "")))
        return out

    def _save_placed(self, record: PlacedRecord) -> None:
        records = [r for r in self._load_placed() if not same_path(r["target"], record.target)]
        records.append(
            {
                "target": record.target,
                "placed_at": record.placed_at,
                "files": [{"rel": f.rel, "sha256": f.sha256, "size": f.size} for f in record.files],
            }
        )
        atomic_write_bytes(
            self.placed_path, json.dumps(records, ensure_ascii=False, indent=1).encode("utf-8")
        )

    def _load_placed(self) -> list[dict]:
        if not self.placed_path.exists():
            return []
        try:
            data = json.loads(self.placed_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return ""
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# «Витрина» для режима обманки
# --------------------------------------------------------------------------- #


@dataclass
class DecoyViewFolder:
    path: str
    name: str = ""
    files: int = 0
    size: int = 0
    note: str = ""
    folder_id: str = ""  # нужен наблюдателю и буферу, секретом не является

    @property
    def human_size(self) -> str:
        return human_size(self.size)

    @property
    def display(self) -> str:
        return display_path(self.path)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "files": self.files,
            "size": self.size,
            "note": self.note,
            "folder_id": self.folder_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DecoyViewFolder":
        return cls(
            path=str(d.get("path", "")),
            name=str(d.get("name", "")),
            files=int(d.get("files", 0)),
            size=int(d.get("size", 0)),
            note=str(d.get("note", "")),
            folder_id=str(d.get("folder_id", "")),
        )


class DecoyView:
    """Что показать, если пароль неверный.

    Список папок не секрет (папки и так видны в проводнике), но файл всё же
    обёрнут ключом приложения, чтобы случайный зевака не прочитал его как есть.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._key = subkey(hashlib.sha256(_APP_SEED).digest(), info=_VIEW_INFO)

    def save(self, folders: list[DecoyViewFolder]) -> None:
        payload = json.dumps([f.to_dict() for f in folders], ensure_ascii=False).encode("utf-8")
        blob = seal(payload, self._key, aad=_VIEW_INFO)
        atomic_write_bytes(self.path, base64.b64encode(blob))

    def load(self) -> list[DecoyViewFolder]:
        if not self.path.exists():
            return []
        try:
            blob = base64.b64decode(self.path.read_bytes())
        except (ValueError, OSError):
            return []
        raw = open_sealed(blob, self._key, aad=_VIEW_INFO)
        if raw is None:
            return []
        try:
            return [DecoyViewFolder.from_dict(d) for d in json.loads(raw.decode("utf-8"))]
        except (ValueError, TypeError):
            return []

    def forget(self) -> None:
        robust_unlink(self.path)

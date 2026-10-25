"""Манифест хранилища: что, откуда и в какой блоб переехало.

Манифест хранится в зашифрованном виде (``manifest.enc``), поэтому имена файлов,
пути и размеры не видны никому, пока не введён пароль.

Внутри все относительные пути используют ``/`` — так манифест переносим
и не зависит от регистра/разделителей Windows.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Iterable

MANIFEST_VERSION = 1


def _to_rel(path: str) -> str:
    return path.replace("\\", "/").strip("/")


@dataclass
class FileEntry:
    rel: str  # относительный путь внутри защищаемой папки
    blob: str  # имя файла-блоба в хранилище
    size: int
    sha256: str
    mtime_ns: int = 0
    attrs: int = 0  # атрибуты Windows (FILE_ATTRIBUTE_*)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rel"] = _to_rel(self.rel)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FileEntry":
        return cls(
            rel=_to_rel(str(d["rel"])),
            blob=str(d["blob"]),
            size=int(d.get("size", 0)),
            sha256=str(d.get("sha256", "")),
            mtime_ns=int(d.get("mtime_ns", 0)),
            attrs=int(d.get("attrs", 0)),
        )


@dataclass
class DirEntry:
    """Пустые каталоги тоже запоминаем — структура папки восстанавливается 1:1."""

    rel: str
    mtime_ns: int = 0
    attrs: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rel"] = _to_rel(self.rel)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DirEntry":
        return cls(rel=_to_rel(str(d["rel"])), mtime_ns=int(d.get("mtime_ns", 0)), attrs=int(d.get("attrs", 0)))


@dataclass
class FolderRecord:
    id: str
    path: str  # исходный абсолютный путь папки
    added_at: float
    locked: bool = True
    files: list[FileEntry] = field(default_factory=list)
    dirs: list[DirEntry] = field(default_factory=list)
    note: str = ""
    decoy_set: str = ""  # имя набора приманок (пусто = общий набор)

    # -- производные ------------------------------------------------------ #
    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def name(self) -> str:
        p = self.path.replace("\\", "/").rstrip("/")
        return p.rsplit("/", 1)[-1] or p

    def blob_ids(self) -> Iterable[str]:
        return (f.blob for f in self.files)

    # -- сериализация ----------------------------------------------------- #
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "added_at": self.added_at,
            "locked": self.locked,
            "note": self.note,
            "decoy_set": self.decoy_set,
            "files": [f.to_dict() for f in self.files],
            "dirs": [d.to_dict() for d in self.dirs],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FolderRecord":
        return cls(
            id=str(d["id"]),
            path=str(d["path"]),
            added_at=float(d.get("added_at", 0.0)),
            locked=bool(d.get("locked", True)),
            note=str(d.get("note", "")),
            decoy_set=str(d.get("decoy_set", "")),
            files=[FileEntry.from_dict(x) for x in d.get("files", [])],
            dirs=[DirEntry.from_dict(x) for x in d.get("dirs", [])],
        )


@dataclass
class Manifest:
    created_at: float = 0.0
    updated_at: float = 0.0
    version: int = MANIFEST_VERSION
    folders: list[FolderRecord] = field(default_factory=list)

    # -- доступ ----------------------------------------------------------- #
    def find(self, folder_id: str) -> FolderRecord | None:
        for f in self.folders:
            if f.id == folder_id:
                return f
        return None

    def find_by_path(self, path: str) -> FolderRecord | None:
        from .fsutil import same_path

        for f in self.folders:
            if same_path(f.path, path):
                return f
        return None

    def all_blob_ids(self) -> set[str]:
        out: set[str] = set()
        for f in self.folders:
            out.update(f.blob_ids())
        return out

    @property
    def locked_folders(self) -> list[FolderRecord]:
        return [f for f in self.folders if f.locked]

    @property
    def unlocked_folders(self) -> list[FolderRecord]:
        return [f for f in self.folders if not f.locked]

    # -- сериализация ----------------------------------------------------- #
    def to_json(self) -> bytes:
        payload = {
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "folders": [f.to_dict() for f in self.folders],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes | str) -> "Manifest":
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("manifest root must be an object")
        version = int(payload.get("version", MANIFEST_VERSION))
        if version > MANIFEST_VERSION:
            raise ValueError(f"manifest version {version} is newer than supported {MANIFEST_VERSION}")
        return cls(
            version=version,
            created_at=float(payload.get("created_at", 0.0)),
            updated_at=float(payload.get("updated_at", 0.0)),
            folders=[FolderRecord.from_dict(d) for d in payload.get("folders", [])],
        )

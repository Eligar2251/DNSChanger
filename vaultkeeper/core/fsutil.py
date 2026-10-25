"""Файловые утилиты: обход папок, атомарная запись, безопасное стирание.

Здесь же — вся защита от «выстрела в ногу»:
 * symlink/junction НИКОГДА не проходим (иначе можно стереть данные за пределами папки);
 * путь к стиранию допускается только внутри явно указанной папки;
 * ``os.replace`` на Windows выполняется с повторами — антивирус/индексатор
   часто держит файл пару миллисекунд.
"""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_WIPE_CHUNK = 1 << 20


# --------------------------------------------------------------------------- #
# Пути
# --------------------------------------------------------------------------- #


def normalize(path: str | os.PathLike) -> str:
    """Абсолютный нормализованный путь; на Windows — ещё и в нижнем регистре для сравнения."""
    p = os.path.abspath(os.path.expandvars(os.fspath(path)))
    if IS_WINDOWS:
        p = p.replace("/", "\\")
        p = os.path.normcase(p)
    return p


def display_path(path: str | os.PathLike) -> str:
    """Путь в том виде, в котором его приятно показывать (домашняя папка → ``~``)."""
    p = os.path.abspath(os.fspath(path))
    home = os.path.expanduser("~")
    if home and home != "~" and is_subpath(p, home):
        tail = p[len(home) :].lstrip("\\/")
        sep = "\\" if IS_WINDOWS else "/"
        return (f"~{sep}{tail}") if tail else "~"
    return p


def same_path(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    return normalize(a) == normalize(b)


def is_subpath(child: str | os.PathLike, parent: str | os.PathLike) -> bool:
    child_n, parent_n = normalize(child), normalize(parent)
    if child_n == parent_n:
        return True
    sep = "\\" if IS_WINDOWS else os.sep
    if not parent_n.endswith(sep):
        parent_n += sep
    return child_n.startswith(parent_n)


def long_path(path: str | os.PathLike) -> str:
    r"""На Windows добавляет префикс ``\\?\``, чтобы работать с путями длиннее 260 символов."""
    p = os.fspath(path)
    if not IS_WINDOWS or p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):  # UNC
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + os.path.abspath(p)


def ensure_dir(path: str | os.PathLike) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def free_space(path: str | os.PathLike) -> int:
    target = Path(path)
    while not target.exists():
        parent = target.parent
        if parent == target:
            break
        target = parent
    try:
        return shutil.disk_usage(str(target)).free
    except OSError:
        return 0


def human_size(n: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if abs(n) < 1024 or unit == "ТБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


# --------------------------------------------------------------------------- #
# Атрибуты Windows
# --------------------------------------------------------------------------- #


def get_attrs(path: str | os.PathLike) -> int:
    if not IS_WINDOWS:
        try:
            return 0 if not os.path.islink(path) else FILE_ATTRIBUTE_REPARSE_POINT
        except OSError:
            return 0
    attrs = ctypes.windll.kernel32.GetFileAttributesW(long_path(path))  # type: ignore[attr-defined]
    return 0 if attrs == -1 else int(attrs)


def set_attrs(path: str | os.PathLike, attrs: int) -> bool:
    if not IS_WINDOWS:
        return False
    return bool(ctypes.windll.kernel32.SetFileAttributesW(long_path(path), int(attrs)))  # type: ignore[attr-defined]


def is_reparse_point(path: str | os.PathLike) -> bool:
    """Симлинк/junction/точка подключения — внутрь таких каталогов не заходим.

    На Windows это FILE_ATTRIBUTE_REPARSE_POINT (junction, symlink, WOF-сжатие),
    в POSIX — симлинк. Важно: проверяем именно тип файла через S_ISLNK,
    а не побитовым И с маской S_IFLNK (иначе обычный файл «похож» на ссылку).
    """
    p = os.fspath(path)
    if IS_WINDOWS:
        return bool(get_attrs(p) & FILE_ATTRIBUTE_REPARSE_POINT)
    try:
        mode = os.lstat(p).st_mode
    except OSError:
        return False
    if stat.S_ISLNK(mode):
        return True
    # сокеты/FIFO шифровать бессмысленно — относим их к «непроходимым»
    return bool(stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode))


def clear_readonly(path: str | os.PathLike) -> None:
    """Снимает «только чтение». ВАЖНО: на симлинках не работает вообще.

    os.chmod по симлинку меняет права ЦЕЛИ, а не ссылки — так можно
    случайно испортить каталог за пределами защищаемой папки.
    """
    p = os.fspath(path)
    if os.path.islink(p):
        return
    try:
        if IS_WINDOWS:
            attrs = get_attrs(path)
            if attrs & FILE_ATTRIBUTE_READONLY:
                set_attrs(path, attrs & ~FILE_ATTRIBUTE_READONLY)
        else:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def set_hidden_system(path: str | os.PathLike, hidden: bool = True) -> bool:
    """Прячет/раскрывает служебный файл атрибутами H+S.

    Только Windows: в других ОС атрибутной модели «скрытый/системный» нет,
    а переименовывать каталоги хранилища было бы опасно — поэтому no-op.
    """
    if not IS_WINDOWS:
        return False
    attrs = get_attrs(path)
    if attrs == 0:
        return False
    if hidden:
        target = attrs | FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
    else:
        target = attrs & ~FILE_ATTRIBUTE_HIDDEN & ~FILE_ATTRIBUTE_SYSTEM
    return set_attrs(path, target)


# --------------------------------------------------------------------------- #
# Обход дерева
# --------------------------------------------------------------------------- #


@dataclass
class WalkItem:
    rel: str  # относительный путь, всегда через "/"
    path: Path
    size: int
    mtime_ns: int
    attrs: int


@dataclass
class WalkResult:
    files: list[WalkItem] = field(default_factory=list)
    dirs: list[WalkItem] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (rel, причина)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def ok(self) -> bool:
        return not self.skipped


def walk_tree(root: str | os.PathLike, follow_links: bool = False) -> WalkResult:
    """Обходит папку, не заходя в symlink/junction и не поднимаясь наверх.

    Всё, что не удалось прочитать, попадает в ``skipped`` — вызывающий код
    обязан отменить операцию, потому что частичная защита хуже никакой.
    """
    root_p = Path(long_path(root)) if IS_WINDOWS else Path(root)
    result = WalkResult()
    root_norm = normalize(root)

    def rel_of(p: Path) -> str:
        try:
            return str(p.relative_to(root_p)).replace("\\", "/")
        except ValueError:
            return p.name

    for dirpath, dirnames, filenames in os.walk(str(root_p), topdown=True, followlinks=follow_links):
        dp = Path(dirpath)
        if not same_path(dp, root_norm) and not is_subpath(dp, root_norm):
            # защита от неожиданного выхода за пределы папки
            result.skipped.append((rel_of(dp), "путь вне защищаемой папки"))
            dirnames[:] = []
            continue

        keep: list[str] = []
        for name in sorted(dirnames):
            child = dp / name
            if is_reparse_point(child):
                result.skipped.append((rel_of(child) or name, "ссылка/junction — пропущено"))
                continue
            try:
                st = child.stat()
            except OSError as exc:
                result.skipped.append((rel_of(child) or name, f"нет доступа: {exc.strerror or exc}"))
                continue
            result.dirs.append(
                WalkItem(rel=rel_of(child), path=child, size=0, mtime_ns=st.st_mtime_ns, attrs=get_attrs(child))
            )
            keep.append(name)
        dirnames[:] = keep

        for name in sorted(filenames):
            child = dp / name
            rel = rel_of(child) or name
            if is_reparse_point(child):
                result.skipped.append((rel, "ссылка/junction — пропущено"))
                continue
            try:
                st = child.stat()
                with open(child, "rb"):
                    pass  # проверка читабельности до начала долгой операции
            except OSError as exc:
                reason = exc.strerror or str(exc)
                result.skipped.append((rel, f"нет доступа: {reason}"))
                continue
            result.files.append(
                WalkItem(rel=rel, path=child, size=st.st_size, mtime_ns=st.st_mtime_ns, attrs=get_attrs(child))
            )

    result.files.sort(key=lambda i: i.rel)
    result.dirs.sort(key=lambda i: i.rel)
    return result


# --------------------------------------------------------------------------- #
# Атомарная запись
# --------------------------------------------------------------------------- #


def fsync_dir(path: str | os.PathLike) -> None:
    if IS_WINDOWS:
        return
    try:
        fd = os.open(os.fspath(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: str | os.PathLike, data: bytes) -> None:
    """Пишет во временный файл, fsync, затем переименовывает — без «половинок»."""
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    robust_replace(tmp, p)
    fsync_dir(p.parent)


def robust_replace(src: str | os.PathLike, dst: str | os.PathLike, retries: int = 8, delay: float = 0.03) -> None:
    """``os.replace`` с повторами: на Windows файл могут кратковременно держать AV/индексатор."""
    src, dst = os.fspath(src), os.fspath(dst)
    last: OSError | None = None
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EPERM, errno.EBUSY, getattr(errno, "WSAEACCES", -1)):
                raise
            last = exc
            time.sleep(delay * (attempt + 1))
    if last is not None:
        raise last


def robust_unlink(path: str | os.PathLike, retries: int = 5, delay: float = 0.03) -> bool:
    for attempt in range(retries):
        try:
            clear_readonly(path)
            os.unlink(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(delay * (attempt + 1))
    return False


# --------------------------------------------------------------------------- #
# Безопасное стирание
# --------------------------------------------------------------------------- #


def secure_delete_file(path: str | os.PathLike, passes: int = 1) -> bool:
    """Перезаписывает содержимое мусором и удаляет файл.

    Важно честно понимать предел: на SSD с wear-leveling и в теневых копиях
    Windows старые блоки могут остаться. Поэтому основная защита здесь —
    не стирание, а то, что единственная копия данных лежит в шифрованном
    контейнере. Стирание убирает «лёгкие» следы (корзина, кэш, проводник).
    """
    p = Path(path)
    if not p.exists() and not p.is_symlink():
        return True
    try:
        clear_readonly(p)
        size = p.stat().st_size
        if size > 0:
            with open(p, "r+b") as fh:
                for _ in range(max(1, passes)):
                    fh.seek(0)
                    left = size
                    while left > 0:
                        block = min(_WIPE_CHUNK, left)
                        fh.write(os.urandom(block))
                        left -= block
                    fh.flush()
                    os.fsync(fh.fileno())
                # финальные нули и обрезка до нуля — чтобы не осталось и размера
                fh.seek(0)
                fh.truncate(0)
                fh.flush()
                os.fsync(fh.fileno())
    except OSError:
        pass
    return robust_unlink(p)


def secure_delete_tree(root: str | os.PathLike, passes: int = 1, keep_root: bool = True) -> int:
    """Стирает всё содержимое папки (снизу вверх), саму папку по умолчанию оставляет.

    Возвращает число удалённых файлов. В symlink/junction не заходит.
    """
    root_p = Path(root)
    if not root_p.exists():
        return 0
    removed = 0
    for dirpath, dirnames, filenames in os.walk(str(root_p), topdown=False, followlinks=False):
        dp = Path(dirpath)
        if not same_path(dp, normalize(root)) and not is_subpath(dp, normalize(root)):
            continue
        for name in filenames:
            child = dp / name
            if is_reparse_point(child):
                robust_unlink(child)  # саму ссылку снять безопасно
                continue
            if secure_delete_file(child, passes=passes):
                removed += 1
        for name in dirnames:
            child = dp / name
            if is_reparse_point(child):
                robust_unlink(child)
                continue
            clear_readonly(child)
            try:
                child.rmdir()
            except OSError:
                pass
    if not keep_root:
        clear_readonly(root_p)
        try:
            root_p.rmdir()
        except OSError:
            pass
    return removed


def rel_to_os(rel: str) -> str:
    """``"a/b/c"`` → путь текущей ОС."""
    return rel.replace("/", os.sep) if os.sep != "/" else rel


def touch_mtime(path: str | os.PathLike, mtime_ns: int) -> None:
    if mtime_ns <= 0:
        return
    try:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    except OSError:
        pass

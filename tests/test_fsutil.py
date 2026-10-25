"""Тесты файловых утилит: обход, стирание, атомарность, границы путей."""

from __future__ import annotations

import os
import stat as statmod
from pathlib import Path

import pytest

from vaultkeeper.core.fsutil import (
    atomic_write_bytes,
    display_path,
    free_space,
    human_size,
    is_reparse_point,
    is_subpath,
    normalize,
    robust_replace,
    robust_unlink,
    same_path,
    secure_delete_file,
    secure_delete_tree,
    walk_tree,
)

from .conftest import make_tree


def test_walk_tree_basic(tmp_path: Path):
    make_tree(tmp_path / "t", {"a.txt": "1", "sub/b.txt": "2", "sub/deep/c.txt": "3", "empty/.keep": ""})
    res = walk_tree(tmp_path / "t")
    assert [f.rel for f in res.files] == ["a.txt", "empty/.keep", "sub/b.txt", "sub/deep/c.txt"]
    assert res.ok
    assert any(d.rel == "sub/deep" for d in res.dirs)


def test_walk_tree_does_not_follow_symlinks(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_text("не трогать", encoding="utf-8")
    target = tmp_path / "t"
    target.mkdir()
    (target / "own.txt").write_text("своё", encoding="utf-8")
    os.symlink(str(outside), str(target / "link_dir"))
    os.symlink(str(outside / "victim.txt"), str(target / "link_file"))

    res = walk_tree(target)
    assert [f.rel for f in res.files] == ["own.txt"]
    assert not res.ok
    assert len(res.skipped) == 2
    assert (outside / "victim.txt").exists()


def test_is_reparse_point_false_for_regular(tmp_path: Path):
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    assert not is_reparse_point(f)
    d = tmp_path / "d"
    d.mkdir()
    assert not is_reparse_point(d)


def test_secure_delete_file_removes_content_and_file(tmp_path: Path):
    f = tmp_path / "secret.bin"
    payload = os.urandom(300_000)
    f.write_bytes(payload)
    assert secure_delete_file(f)
    assert not f.exists()


def test_secure_delete_file_readonly(tmp_path: Path):
    if os.geteuid() == 0:
        pytest.skip("root игнорирует права")
    f = tmp_path / "ro.bin"
    f.write_bytes(b"data")
    os.chmod(f, statmod.S_IREAD)
    assert secure_delete_file(f)
    assert not f.exists()


def test_secure_delete_tree_keeps_root(tmp_path: Path):
    root = tmp_path / "tree"
    make_tree(root, {"a.txt": "1", "sub/b.txt": "2"})
    removed = secure_delete_tree(root, keep_root=True)
    assert removed == 2
    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_secure_delete_tree_does_not_follow_junction_like_symlink(tmp_path: Path):
    outside = tmp_path / "outside"
    make_tree(outside, {"keep.txt": "важное"})
    root = tmp_path / "tree"
    root.mkdir()
    os.symlink(str(outside), str(root / "escape"))
    secure_delete_tree(root)
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "важное"


def test_atomic_write_bytes_creates_and_replaces(tmp_path: Path):
    p = tmp_path / "sub" / "file.bin"
    atomic_write_bytes(p, b"first")
    assert p.read_bytes() == b"first"
    atomic_write_bytes(p, b"second")
    assert p.read_bytes() == b"second"
    assert not list(p.parent.glob("*.tmp*"))


def test_atomic_write_survives_partial(tmp_path: Path):
    p = tmp_path / "f.bin"
    atomic_write_bytes(p, b"old")
    # имитируем сбой: временный файл остался
    tmp = p.with_name(p.name + ".tmp999")
    tmp.write_bytes(b"half")
    atomic_write_bytes(p, b"new")
    assert p.read_bytes() == b"new"
    robust_unlink(tmp)


def test_robust_replace(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_bytes(b"1")
    robust_replace(a, b)
    assert b.read_bytes() == b"1"
    assert not a.exists()


def test_paths_helpers(tmp_path: Path):
    assert same_path(str(tmp_path / "x"), str(tmp_path / "x"))
    assert is_subpath(tmp_path / "a" / "b", tmp_path / "a")
    assert not is_subpath(tmp_path / "a", tmp_path / "a" / "b")
    assert not is_subpath(tmp_path / "ab", tmp_path / "a")
    assert normalize(str(tmp_path)) == normalize(str(tmp_path) + os.sep)


def test_human_size():
    assert human_size(0) == "0 Б"
    assert human_size(1023) == "1023 Б"
    assert "КБ" in human_size(2048)
    assert "МБ" in human_size(5 * 1024 * 1024)
    assert "ГБ" in human_size(3 * 1024**3)


def test_display_path_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    if os.name != "nt":
        assert display_path(tmp_path / "x") == "~/x"


def test_free_space_positive(tmp_path: Path):
    assert free_space(tmp_path) > 0


def test_walk_tree_unreadable_file_is_skipped(tmp_path: Path):
    if os.geteuid() == 0:
        pytest.skip("root читает всё")
    root = tmp_path / "t"
    root.mkdir()
    (root / "ok.txt").write_text("ok", encoding="utf-8")
    bad = root / "bad.txt"
    bad.write_text("x", encoding="utf-8")
    os.chmod(bad, 0o000)
    try:
        res = walk_tree(root)
        assert not res.ok
        assert [f.rel for f in res.files] == ["ok.txt"]
    finally:
        os.chmod(bad, 0o644)


def test_secure_delete_never_touches_symlink_target_permissions(tmp_path: Path):
    """Регрессия: clear_readonly на симлинке менял права ЦЕЛИ (каталога за ссылкой)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_text("важное", encoding="utf-8")
    before_mode = outside.stat().st_mode

    root = tmp_path / "tree"
    root.mkdir()
    os.symlink(str(outside), str(root / "escape"))
    secure_delete_tree(root)

    assert outside.stat().st_mode == before_mode
    assert keep.read_text(encoding="utf-8") == "важное"
    assert not (root / "escape").is_symlink()

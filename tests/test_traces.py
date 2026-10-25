"""Тесты зачистки следов (чистая логика + работа с фейковыми каталогами Windows)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vaultkeeper.core.traces import (
    QUICK_ACCESS_DESTINATION,
    TraceCleaner,
    TraceReport,
    data_references,
    file_references,
    is_inside_protected,
    name_matches,
    path_needles,
)


def test_path_needles_contain_utf16_and_ansi():
    needles = path_needles("C:\\Users\\Me\\Secret")
    assert "C:\\Users\\Me\\Secret".encode("utf-16-le") in needles
    assert "C:\\Users\\Me\\Secret".encode("utf-8") in needles


def test_path_needles_include_parents():
    needles = path_needles("/home/x/a/b/c")
    joined = b"".join(needles)
    assert "/home/x/a/b".encode("utf-8") in joined


def test_data_references_detects_utf16():
    payload = b"junk" + "D:\\Docs\\Secret\\file.docx".encode("utf-16-le") + b"tail"
    assert data_references(payload, path_needles("D:\\Docs\\Secret"))


def test_data_references_no_false_positive():
    payload = b"totally unrelated bytes" * 40
    assert not data_references(payload, path_needles("D:\\Docs\\Secret"))


def test_file_references(tmp_path: Path):
    f = tmp_path / "x.automaticDestinations-ms"
    f.write_bytes(b"\x00\x01" + "/tmp/protected".encode("utf-16-le"))
    assert file_references(f, path_needles("/tmp/protected"))
    assert not file_references(f, path_needles("/other/path"))


def test_name_matches_case_insensitive():
    assert name_matches("Отчёт.PDF", {"отчёт.pdf"})
    assert not name_matches("другой.txt", {"отчёт.pdf"})


def test_is_inside_protected():
    assert is_inside_protected("/tmp/p/sub/file.txt", ["/tmp/p"])
    assert not is_inside_protected("/tmp/other", ["/tmp/p"])


# --------------------------------------------------------------------------- #
# Боевые действия на фейковой структуре каталогов
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fake_windows(tmp_path: Path) -> tuple[TraceCleaner, Path]:
    appdata = tmp_path / "Roaming"
    local = tmp_path / "Local"
    recent = appdata / "Microsoft" / "Windows" / "Recent"
    (recent / "AutomaticDestinations").mkdir(parents=True)
    (recent / "CustomDestinations").mkdir(parents=True)
    (local / "Microsoft" / "Windows" / "Explorer").mkdir(parents=True)
    return TraceCleaner(appdata=appdata, local_appdata=local), recent


def test_clean_shortcuts_removes_only_matching(fake_windows):
    cleaner, recent = fake_windows
    protected = "/tmp/Secret"
    bad = recent / "secret.docx.lnk"
    bad.write_bytes(b"\x00" + protected.encode("utf-16-le"))
    good = recent / "music.mp3.lnk"
    good.write_bytes(b"\x00" + "C:\\Music\\track.mp3".encode("utf-16-le"))

    removed, failed = cleaner.clean_shortcuts([protected])
    assert removed == 1
    assert not bad.exists()
    assert good.exists()


def test_clean_jumplists_removes_quick_access_and_matches(fake_windows):
    cleaner, recent = fake_windows
    protected = "/tmp/Secret"
    qa = recent / "AutomaticDestinations" / QUICK_ACCESS_DESTINATION
    qa.write_bytes(b"x" * 10)
    match = recent / "CustomDestinations" / "abcd.customDestinations-ms"
    match.write_bytes(protected.encode("utf-16-le"))
    other = recent / "AutomaticDestinations" / "deadbeef.automaticDestinations-ms"
    other.write_bytes(b"unrelated")

    removed, failed = cleaner.clean_jumplists([protected])
    assert removed == 2
    assert not qa.exists()
    assert not match.exists()
    assert other.exists()


def test_clean_thumbcache(fake_windows):
    cleaner, recent = fake_windows
    explorer = cleaner.thumb_dir
    (explorer / "thumbcache_32.db").write_bytes(b"1")
    (explorer / "thumbcache_256.db").write_bytes(b"2")
    (explorer / "other.db").write_bytes(b"3")

    removed, blocked = cleaner.clean_thumbcache()
    assert removed == 2
    assert blocked is False
    assert (explorer / "other.db").exists()


def test_purge_returns_report(fake_windows):
    cleaner, recent = fake_windows
    protected = "/tmp/Secret"
    (recent / "a.lnk").write_bytes(protected.encode("utf-16-le"))
    report = cleaner.purge([protected], clean_thumbs=True, clean_mru=False, quick_access_off=False)
    assert isinstance(report, TraceReport)
    assert report.shortcuts_removed == 1
    assert report.total >= 1
    assert "ярлыков: 1" in report.summary()


def test_purge_without_paths_is_safe(fake_windows):
    cleaner, _ = fake_windows
    report = cleaner.purge([], clean_thumbs=False, clean_mru=False, quick_access_off=False)
    assert report.total == 0


def test_recent_dir_missing_is_safe(tmp_path: Path):
    cleaner = TraceCleaner(appdata=tmp_path / "nope", local_appdata=tmp_path / "nope2")
    removed, failed = cleaner.clean_shortcuts(["/tmp/x"])
    assert removed == 0
    assert cleaner.clean_thumbcache() == (0, False)


def test_binary_with_only_ascii_path_detected(tmp_path: Path):
    f = tmp_path / "x.lnk"
    f.write_bytes(b"prefix" + b"D:\\Secret\\file.txt" + b"suffix")
    assert file_references(f, path_needles("D:\\Secret"))


def test_needles_ignore_empty():
    assert path_needles("") == []
    assert path_needles("ab") != []  # короткий путь нормализуется до абсолютного


def test_os_environ_defaults_used():
    cleaner = TraceCleaner()
    assert cleaner.recent_dir.name == "Recent"
    assert os.sep in str(cleaner.recent_dir) or "/" in str(cleaner.recent_dir)

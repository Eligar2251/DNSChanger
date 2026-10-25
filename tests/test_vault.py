"""Тесты хранилища: защита папок, цикл блокировки, обманки, аварийное восстановление."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vaultkeeper.core import blob as blobmod
from vaultkeeper.core import crypto
from vaultkeeper.core.decoys import DecoyViewFolder
from vaultkeeper.core.errors import EmergencyUnavailable, FolderError, VaultNotFound
from vaultkeeper.core.fsutil import walk_tree
from vaultkeeper.core.manifest import Manifest
from vaultkeeper.core.vault import Vault

from .conftest import PASSWORD, TEST_ITERATIONS, MemoryEmergencyStore, make_tree


# --------------------------------------------------------------------------- #
# Создание и пароли
# --------------------------------------------------------------------------- #


def test_create_produces_encrypted_header_and_manifest(vault_root: Path):
    v, code = Vault.create(vault_root, PASSWORD, iterations=TEST_ITERATIONS)
    assert (vault_root / "header.bin").exists()
    assert (vault_root / "manifest.enc").exists()
    assert (vault_root / "recovery.enc").exists()
    assert v.unlocked
    assert code and "-" in code
    # манифест на диске — не читаемый JSON
    raw = (vault_root / "manifest.enc").read_bytes()
    with pytest.raises((UnicodeDecodeError, ValueError)):
        json.loads(raw)


def test_load_without_password_gives_nothing(vault_root: Path):
    Vault.create(vault_root, PASSWORD, iterations=TEST_ITERATIONS)
    v = Vault.load(vault_root)
    assert not v.unlocked
    with pytest.raises(Exception):
        _ = v.manifest


def test_missing_vault_raises(vault_root: Path):
    with pytest.raises(VaultNotFound):
        Vault.load(vault_root / "nope")


def test_double_create_rejected(vault_root: Path):
    Vault.create(vault_root, PASSWORD, iterations=TEST_ITERATIONS)
    with pytest.raises(Exception):
        Vault.create(vault_root, PASSWORD, iterations=TEST_ITERATIONS)


def test_short_password_rejected(vault_root: Path):
    with pytest.raises(Exception):
        Vault.create(vault_root / "v2", "abc", iterations=TEST_ITERATIONS)


def test_unlock_wrong_password_is_silent_decoy_mode(vault: Vault):
    vault.forget_keys()
    outcome = vault.unlock("совершенно-не-тот-пароль")
    assert outcome.authenticated is False
    assert outcome.decoy_mode is True
    assert outcome.message == "ok"  # никакой «ошибки» наружу
    assert not vault.unlocked


def test_unlock_correct_password(vault: Vault):
    vault.forget_keys()
    outcome = vault.unlock(PASSWORD)
    assert outcome.authenticated
    assert outcome.decoy_mode is False
    assert vault.unlocked


def test_change_password(vault: Vault):
    vault.change_password(PASSWORD, "новый-пароль-123")
    vault.forget_keys()
    assert vault.unlock(PASSWORD).authenticated is False
    assert vault.unlock("новый-пароль-123").authenticated is True


def test_change_password_with_wrong_old_rejected(vault: Vault):
    with pytest.raises(Exception):
        vault.change_password("неверный", "новый-пароль-123")


# --------------------------------------------------------------------------- #
# Защита папки
# --------------------------------------------------------------------------- #


def test_add_folder_encrypts_and_wipes_originals(vault: Vault, work_dir: Path):
    target = work_dir / "Секреты"
    make_tree(target, {"документ.txt": "очень важно", "photo/2024/a.bin": 4096, "empty/keep/note.md": "# hi"})
    before = {p.name: p.read_bytes() for p in target.rglob("*") if p.is_file()}

    rec = vault.add_folder(target, verify_deep=True)

    # originals gone
    remaining = [p for p in target.rglob("*") if p.is_file()]
    for p in remaining:
        assert p.read_bytes() not in before.values(), "настоящий файл остался на диске!"
    # структура папок с данными исчезла
    assert not (target / "photo").exists()
    # блобы созданы
    blobs = list((vault.blobs_dir).glob("*.vkb"))
    assert len(blobs) == 3
    assert rec.file_count == 3
    assert rec.locked is True
    # содержимое блобов не совпадает с открытым текстом
    plaintext_marks = [
        "очень важно".encode("utf-8"),
        "документ.txt".encode("utf-8"),
        "документ.txt".encode("utf-16-le"),
    ]
    for b in blobs:
        data = b.read_bytes()
        for mark in plaintext_marks:
            assert mark not in data


def test_added_folder_contains_decoys(vault: Vault, work_dir: Path):
    target = work_dir / "Секреты"
    make_tree(target, {"secret.txt": "top secret"})
    vault.add_folder(target)
    names = {p.name for p in target.iterdir()}
    assert names, "папка должна быть не пустой — в ней приманки"
    assert "Заметки.txt" in names
    assert "secret.txt" not in names
    assert "top secret".encode("utf-8") not in b"".join(p.read_bytes() for p in target.rglob("*") if p.is_file())


def test_unlock_restores_files_exactly(vault: Vault, work_dir: Path):
    target = work_dir / "Секреты"
    make_tree(
        target,
        {"a.txt": "первый", "sub/b.txt": "второй", "sub/deep/c.bin": 8192, "sub/emptydir/.keep": ""},
    )
    original = {
        str(p.relative_to(target)).replace("\\", "/"): p.read_bytes() for p in target.rglob("*") if p.is_file()
    }
    mtimes = {
        str(p.relative_to(target)).replace("\\", "/"): p.stat().st_mtime_ns
        for p in target.rglob("*")
        if p.is_file()
    }

    vault.add_folder(target)
    assert (target / "sub" / "deep").exists() is False

    vault.unlock_all()

    restored = {
        str(p.relative_to(target)).replace("\\", "/"): p.read_bytes() for p in target.rglob("*") if p.is_file()
    }
    assert restored == original
    # приманки убраны
    assert not (target / "Заметки.txt").exists()
    # пустой каталог восстановлен
    assert (target / "sub" / "emptydir").is_dir()
    # mtime сохранён
    for rel, mt in mtimes.items():
        assert (target / Path(*rel.split("/"))).stat().st_mtime_ns == mt


def test_lock_unlock_cycle_is_stable(vault: Vault, work_dir: Path):
    target = work_dir / "Cycle"
    make_tree(target, {"f.bin": os.urandom(50_000), "g.txt": "текст"})
    original = {p.name: p.read_bytes() for p in target.rglob("*") if p.is_file()}

    vault.add_folder(target)
    for _ in range(3):
        vault.unlock_all()
        assert {p.name: p.read_bytes() for p in target.rglob("*") if p.is_file()} == original
        vault.lock_all()
        assert not any(p.name in original for p in target.iterdir() if p.is_file())


def test_edited_file_is_reencrypted_not_stale(vault: Vault, work_dir: Path):
    target = work_dir / "Edit"
    f = target / "note.txt"
    target.mkdir(parents=True, exist_ok=True)
    f.write_text("версия 1", encoding="utf-8")
    vault.add_folder(target)

    vault.unlock_all()
    f.write_text("версия 2 — изменён пользователем", encoding="utf-8")
    vault.lock_all()
    vault.unlock_all()

    assert f.read_text(encoding="utf-8") == "версия 2 — изменён пользователем"


def test_new_and_deleted_files_handled(vault: Vault, work_dir: Path):
    target = work_dir / "Delta"
    target.mkdir(parents=True, exist_ok=True)
    (target / "old.txt").write_text("старый", encoding="utf-8")
    vault.add_folder(target)

    vault.unlock_all()
    (target / "old.txt").unlink()
    (target / "new.txt").write_text("новый", encoding="utf-8")
    (target / "sub").mkdir()
    (target / "sub" / "inner.bin").write_bytes(os.urandom(1024))
    vault.lock_all()

    assert len(vault.manifest.folders[0].files) == 2
    vault.unlock_all()
    assert not (target / "old.txt").exists()
    assert (target / "new.txt").read_text(encoding="utf-8") == "новый"
    assert (target / "sub" / "inner.bin").stat().st_size == 1024
    # старых блобов не осталось
    assert len(list(vault.blobs_dir.glob("*.vkb"))) == 2


def test_blobs_survive_while_unlocked_as_backup(vault: Vault, work_dir: Path):
    """Разблокировка не удаляет блобы — это резервная копия до следующей блокировки."""
    target = work_dir / "Backup"
    make_tree(target, {"f.txt": "данные"})
    vault.add_folder(target)
    n = len(list(vault.blobs_dir.glob("*.vkb")))
    vault.unlock_all()
    assert len(list(vault.blobs_dir.glob("*.vkb"))) == n


# --------------------------------------------------------------------------- #
# Безопасность границ
# --------------------------------------------------------------------------- #


def test_symlink_outside_is_not_followed_or_wiped(vault: Vault, work_dir: Path):
    outside = work_dir / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("не трогать!", encoding="utf-8")

    target = work_dir / "Linked"
    target.mkdir()
    (target / "own.txt").write_text("своё", encoding="utf-8")
    os.symlink(str(outside), str(target / "escape"))

    with pytest.raises(FolderError):
        vault.add_folder(target)

    # данные вне папки целы, и сама папка не тронута
    assert victim.read_text(encoding="utf-8") == "не трогать!"
    assert (target / "own.txt").read_text(encoding="utf-8") == "своё"


def test_unreadable_file_aborts_and_keeps_data(vault: Vault, work_dir: Path):
    if os.geteuid() == 0:  # root читает всё — тест не показателен
        pytest.skip("running as root")
    target = work_dir / "Perms"
    target.mkdir()
    good = target / "good.txt"
    good.write_text("ok", encoding="utf-8")
    bad = target / "bad.txt"
    bad.write_text("secret", encoding="utf-8")
    os.chmod(bad, 0o000)

    try:
        with pytest.raises(FolderError):
            vault.add_folder(target)
        assert good.read_text(encoding="utf-8") == "ok"
        os.chmod(bad, 0o644)
        assert bad.read_text(encoding="utf-8") == "secret"
    finally:
        os.chmod(bad, 0o644)


def test_validate_folder_rules(vault: Vault, work_dir: Path, vault_root: Path):
    target = work_dir / "T"
    target.mkdir()
    vault.add_folder(target)

    assert vault.validate_folder(target)  # уже защищена
    nested = target / "inner"
    nested.mkdir()
    assert vault.validate_folder(nested)  # внутри защищённой
    assert vault.validate_folder(work_dir)  # содержит защищённую
    assert vault.validate_folder(vault_root)  # само хранилище
    assert vault.validate_folder(work_dir / "missing")
    assert vault.validate_folder(work_dir) != []
    fresh = work_dir / "fresh"
    fresh.mkdir()
    assert vault.validate_folder(fresh) == []


def test_vault_root_cannot_be_protected(vault: Vault, vault_root: Path):
    assert vault.validate_folder(vault_root)


# --------------------------------------------------------------------------- #
# Режим обманки
# --------------------------------------------------------------------------- #


def test_decoy_view_lists_protected_folders(vault: Vault, work_dir: Path):
    target = work_dir / "Секреты"
    make_tree(target, {"a.txt": "1", "b.txt": "22"})
    vault.add_folder(target)
    vault.forget_keys()

    outcome = vault.unlock("неверный")
    assert outcome.decoy_mode
    assert len(outcome.decoy_folders) == 1
    shown = outcome.decoy_folders[0]
    assert Path(shown.path).name == "Секреты"
    assert shown.files == 2
    # на диске в этот момент лежат только приманки
    visible = {p.name for p in target.iterdir()}
    assert "a.txt" not in visible and "b.txt" not in visible
    assert "Заметки.txt" in visible


def test_decoys_can_be_custom(vault: Vault, work_dir: Path):
    decoy_src = work_dir / "decoy_src.txt"
    decoy_src.write_text("приманка", encoding="utf-8")
    name = vault.decoys.add(decoy_src)
    assert name == "decoy_src.txt"

    target = work_dir / "Секреты"
    target.mkdir()
    (target / "real.txt").write_text("настоящее", encoding="utf-8")
    vault.add_folder(target)

    names = {p.name for p in target.iterdir()}
    assert "decoy_src.txt" in names
    assert "real.txt" not in names

    vault.unlock_all()
    assert (target / "real.txt").read_text(encoding="utf-8") == "настоящее"
    assert not (target / "decoy_src.txt").exists()


def test_decoy_clear_does_not_touch_user_files(vault: Vault, work_dir: Path):
    target = work_dir / "Mix"
    target.mkdir()
    (target / "real.txt").write_text("настоящее", encoding="utf-8")
    vault.add_folder(target)
    # пользователь положил свой файл поверх приманок
    own = target / "мой_файл.txt"
    own.write_text("моё", encoding="utf-8")
    # и переименовал приманку
    (target / "Заметки.txt").rename(target / "Заметки2.txt")

    vault.unlock_all()
    assert own.read_text(encoding="utf-8") == "моё"
    assert (target / "Заметки2.txt").exists()  # изменившийся файл не удаляем
    assert (target / "real.txt").read_text(encoding="utf-8") == "настоящее"


def test_decoy_view_survives_without_password(vault_root: Path, work_dir: Path):
    v, _ = Vault.create(vault_root, PASSWORD, iterations=TEST_ITERATIONS)
    target = work_dir / "X"
    target.mkdir()
    (target / "f.txt").write_text("данные", encoding="utf-8")
    v.add_folder(target)
    v.forget_keys()

    v2 = Vault.load(vault_root)
    outcome = v2.unlock("чужой-пароль")
    assert outcome.decoy_mode
    assert [f.name for f in outcome.decoy_folders] == ["X"]


# --------------------------------------------------------------------------- #
# Аварийное восстановление («убили через диспетчер»)
# --------------------------------------------------------------------------- #


def test_crash_restore_without_password(vault_root: Path, work_dir: Path, emergency):
    v, _ = Vault.create(vault_root, PASSWORD, iterations=TEST_ITERATIONS, emergency_store=emergency)
    target = work_dir / "Секреты"
    make_tree(target, {"a.txt": "важное", "b.bin": 2048})
    original = {p.name: p.read_bytes() for p in target.rglob("*") if p.is_file()}
    v.add_folder(target)
    v.write_state(phase="locked", clean_exit=False)  # имитация kill -9
    v.forget_keys()

    # «новый запуск» после аварийного завершения
    v2 = Vault.load(vault_root, emergency_store=emergency)
    state = v2.begin_session()
    assert state["crashed"] is True
    assert v2.emergency_available

    restored = v2.restore_after_crash()
    assert restored == 1
    got = {p.name: p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert got == original, "файлы не вернулись после аварийного закрытия"


def test_emergency_key_absent_raises(vault_root: Path, work_dir: Path):
    v, _ = Vault.create(vault_root, PASSWORD, iterations=TEST_ITERATIONS, emergency_store=MemoryEmergencyStore())
    v.emergency.forget()
    v.forget_keys()
    with pytest.raises(EmergencyUnavailable):
        v.emergency_unlock()


def test_recovery_code_unlock(vault_root: Path, work_dir: Path):
    v, code = Vault.create(vault_root, PASSWORD, iterations=TEST_ITERATIONS)
    target = work_dir / "R"
    make_tree(target, {"f.txt": "данные"})
    v.add_folder(target)
    v.forget_keys()

    v2 = Vault.load(vault_root)
    with pytest.raises(EmergencyUnavailable):
        v2.unlock_with_recovery_code("XXXX-XXXX-XXXX-XXXX")
    outcome = v2.unlock_with_recovery_code(code)
    assert outcome.authenticated
    v2.unlock_all()
    assert (target / "f.txt").read_text(encoding="utf-8") == "данные"


def test_clean_session_flag(vault: Vault):
    vault.begin_session()
    assert vault.read_state()["clean_exit"] is False
    vault.end_session()
    assert vault.read_state()["clean_exit"] is True
    assert vault.begin_session()["crashed"] is False


# --------------------------------------------------------------------------- #
# Целостность и служебное
# --------------------------------------------------------------------------- #


def test_verify_integrity_clean(vault: Vault, work_dir: Path):
    target = work_dir / "V"
    make_tree(target, {"a.txt": "1", "b.bin": 10_000})
    vault.add_folder(target)
    assert vault.verify_integrity(deep=True) == []


def test_verify_integrity_detects_corruption(vault: Vault, work_dir: Path):
    target = work_dir / "V"
    make_tree(target, {"a.bin": 5000})
    vault.add_folder(target)
    blob = next(vault.blobs_dir.glob("*.vkb"))
    raw = bytearray(blob.read_bytes())
    raw[-4] ^= 0xFF
    blob.write_bytes(bytes(raw))
    problems = vault.verify_integrity(deep=True)
    assert problems


def test_verify_integrity_detects_missing_blob(vault: Vault, work_dir: Path):
    target = work_dir / "V"
    make_tree(target, {"a.txt": "1"})
    vault.add_folder(target)
    next(vault.blobs_dir.glob("*.vkb")).unlink()
    assert vault.verify_integrity()


def test_prune_orphan_blobs(vault: Vault, work_dir: Path):
    target = work_dir / "V"
    make_tree(target, {"a.txt": "1"})
    vault.add_folder(target)
    (vault.blobs_dir / "orphan123.vkb").write_bytes(b"junk")
    assert vault.prune_orphan_blobs() == 1
    assert not (vault.blobs_dir / "orphan123.vkb").exists()


def test_manifest_is_encrypted_and_versioned(vault: Vault, work_dir: Path):
    target = work_dir / "V"
    make_tree(target, {"a.txt": "1"})
    vault.add_folder(target)
    raw = vault.manifest_path.read_bytes()
    assert b"a.txt" not in raw
    assert str(target).encode() not in raw
    assert str(target).encode("utf-16-le") not in raw


def test_manifest_backup_created(vault: Vault, work_dir: Path):
    target = work_dir / "V"
    make_tree(target, {"a.txt": "1"})
    vault.add_folder(target)
    vault.lock_all()
    vault.unlock_all()
    backups = list(vault.backups_dir.glob("manifest-*.enc"))
    assert backups


def test_corrupted_manifest_falls_back_to_backup(vault_root: Path, work_dir: Path):
    v, _ = Vault.create(vault_root, PASSWORD, iterations=TEST_ITERATIONS)
    target = work_dir / "V"
    make_tree(target, {"a.txt": "данные"})
    v.add_folder(target)
    # любая операция, пересохраняющая манифест, оставляет резервную копию
    v.unlock_all()
    assert len(list(v.backups_dir.glob("manifest-*.enc"))) >= 2
    v.manifest_path.write_bytes(os.urandom(200))
    v.forget_keys()

    v2 = Vault.load(vault_root)
    assert v2.unlock(PASSWORD).authenticated
    assert len(v2.manifest.folders) == 1
    v2.unlock_all()
    assert (target / "a.txt").read_text(encoding="utf-8") == "данные"


def test_backup_rotation_keeps_recent(vault: Vault, work_dir: Path):
    from vaultkeeper.core.vault import MAX_MANIFEST_BACKUPS

    for i in range(MAX_MANIFEST_BACKUPS + 6):
        target = work_dir / f"f{i}"
        target.mkdir()
        (target / "a.txt").write_text(str(i), encoding="utf-8")
        vault.add_folder(target)
    backups = list(vault.backups_dir.glob("manifest-*.enc"))
    assert len(backups) <= MAX_MANIFEST_BACKUPS + 1


def test_stats(vault: Vault, work_dir: Path):
    target = work_dir / "S"
    make_tree(target, {"a.txt": "hello"})
    vault.add_folder(target)
    s = vault.stats()
    assert s["folders_total"] == 1
    assert s["folders_locked"] == 1
    assert s["files"] == 1
    assert s["size"] == 5
    assert s["blobs"] == 1


def test_interrupted_operation_detected(vault: Vault, work_dir: Path):
    target = work_dir / "J"
    make_tree(target, {"a.txt": "1"})
    vault.add_folder(target)
    assert vault.interrupted_operation() is None
    vault._journal(op="ingest", folder_id="x", phase="encrypt")
    assert vault.interrupted_operation()["phase"] == "encrypt"


def test_forget_folder_restores_files(vault: Vault, work_dir: Path):
    target = work_dir / "F"
    make_tree(target, {"a.txt": "данные", "sub/b.txt": "ещё"})
    vault.add_folder(target)
    vault.forget_folder(vault.manifest.folders[0].id)
    assert (target / "a.txt").read_text(encoding="utf-8") == "данные"
    assert (target / "sub" / "b.txt").read_text(encoding="utf-8") == "ещё"
    assert vault.manifest.folders == []
    assert list(vault.blobs_dir.glob("*.vkb")) == []
    assert not (target / "Заметки.txt").exists()


def test_big_file_roundtrip(vault: Vault, work_dir: Path):
    """Файл больше куска шифрования (4 МиБ)."""
    target = work_dir / "Big"
    target.mkdir()
    data = os.urandom(9 * 1024 * 1024 + 13)
    (target / "big.bin").write_bytes(data)
    vault.add_folder(target, verify_deep=True)
    assert not (target / "big.bin").exists()
    vault.unlock_all()
    assert (target / "big.bin").read_bytes() == data


def test_unicode_and_deep_names(vault: Vault, work_dir: Path):
    target = work_dir / "Юникод 🎉"
    make_tree(target, {"папка/файл №1 (копия).txt": "привет", "very/deep/nested/tree/leaf.md": "x"})
    original = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    vault.add_folder(target)
    vault.unlock_all()
    restored = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert restored == original

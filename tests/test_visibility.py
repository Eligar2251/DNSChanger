"""Папка должна исчезать полностью: скрытие целиком, витрина обманок, буфер новых файлов.

Это сценарии из требования «посторонний не видит мои папки и файлы в них,
даже если в браузере нажать добавление файлов и показать скрытые».
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vaultkeeper.core.vault import Vault

PASSWORD = "visibility-password"


@pytest.fixture()
def hidden_vault(tmp_path: Path):
    """Сейф, где защищённая папка при блокировке убирается с диска целиком."""
    vault_dir = tmp_path / "vault"
    target = tmp_path / "work" / "Секрет"
    (target / "вложенная").mkdir(parents=True)
    (target / "файл.txt").write_bytes(b"secret-data" * 100)
    (target / "вложенная" / "глубоко.bin").write_bytes(bytes(range(256)))

    vault, _code = Vault.create(vault_dir, PASSWORD, iterations=1000)
    vault.decoys.seed_defaults()
    vault.add_folder(target, purge_traces=False, keep_folder_visible=False)
    yield vault, target
    vault.forget_keys()


# ------------------------------------------------------------------ #
# Полное исчезновение папки
# ------------------------------------------------------------------ #


def test_folder_disappears_completely(hidden_vault):
    vault, target = hidden_vault
    assert not target.exists()
    assert not target.parent.joinpath(target.name).is_dir()
    # витрина всё равно помнит, что здесь была папка (для сеанса обманки)
    view = vault.decoy_view.load()
    assert len(view) == 1
    assert view[0].path == str(target)
    assert view[0].folder_id == vault.manifest.folders[0].id


def test_unlock_brings_folder_back_byte_exact(hidden_vault):
    vault, target = hidden_vault
    vault.forget_keys()
    out = vault.unlock(PASSWORD)
    assert out.authenticated and not out.decoy_mode
    vault.unlock_all()  # именно это возвращает файлы на диск
    assert (target / "файл.txt").read_bytes() == b"secret-data" * 100
    assert (target / "вложенная" / "глубоко.bin").read_bytes() == bytes(range(256))


def test_relock_removes_folder_again(hidden_vault):
    vault, target = hidden_vault
    vault.forget_keys()
    vault.unlock(PASSWORD)
    vault.unlock_all()
    assert target.is_dir()
    vault.lock_all(keep_folder_visible=False)
    assert not target.exists()
    vault.forget_keys()
    vault.unlock(PASSWORD)
    vault.unlock_all()
    assert (target / "файл.txt").read_bytes() == b"secret-data" * 100


# ------------------------------------------------------------------ #
# Витрина обманок: папки появляются только в сеансе с неверным паролем
# ------------------------------------------------------------------ #


def test_decoy_view_materialization_roundtrip(hidden_vault):
    vault, target = hidden_vault
    assert not target.exists()

    assert vault.materialize_decoys_for_view() >= 1
    assert target.is_dir()
    assert list(target.rglob("*")), "в витрине должны лежать файлы-приманки"
    assert not (target / "файл.txt").exists()

    assert vault.clear_decoy_folders() >= 1
    assert not target.exists()


def test_decoy_view_keeps_user_files(hidden_vault, tmp_path: Path):
    """Свои файлы в папке не удаляются, когда обманки убираются."""
    vault, target = hidden_vault
    target.mkdir(parents=True, exist_ok=True)
    mine = target / "моё.txt"
    mine.write_text("не трогать", encoding="utf-8")

    vault.materialize_decoys_for_view()
    vault.clear_decoy_folders()

    assert mine.read_text(encoding="utf-8") == "не трогать"


# ------------------------------------------------------------------ #
# Мгновенная защита: новый файл шифруется сам
# ------------------------------------------------------------------ #


def test_ingest_changes_hides_new_file(tmp_path: Path):
    vault_dir = tmp_path / "vault"
    target = tmp_path / "work" / "Документы"
    target.mkdir(parents=True)
    (target / "старый.txt").write_text("старый", encoding="utf-8")

    vault, _code = Vault.create(vault_dir, PASSWORD, iterations=1000)
    vault.add_folder(target, purge_traces=False)
    vault.forget_keys()
    vault.unlock(PASSWORD)
    vault.unlock_all()

    payload = b"new-file" * 50
    (target / "новый.txt").write_bytes(payload)
    folder_id = vault.manifest.folders[0].id

    assert vault.ingest_changes(folder_id) == 1
    assert not (target / "новый.txt").exists()

    vault.lock_all()
    vault.forget_keys()
    vault.unlock(PASSWORD)
    vault.unlock_all()
    assert (target / "новый.txt").read_bytes() == payload
    assert (target / "старый.txt").read_text(encoding="utf-8") == "старый"
    vault.forget_keys()


def test_ingest_changes_requires_unlocked_vault(tmp_path: Path):
    vault_dir = tmp_path / "vault"
    target = tmp_path / "work" / "Папка"
    target.mkdir(parents=True)
    vault, _code = Vault.create(vault_dir, PASSWORD, iterations=1000)
    vault.add_folder(target, purge_traces=False)
    folder_id = vault.manifest.folders[0].id
    vault.forget_keys()

    with pytest.raises(Exception):
        vault.ingest_changes(folder_id)


# ------------------------------------------------------------------ #
# Буфер: файл убирается мгновенно даже без ключа в памяти
# ------------------------------------------------------------------ #


def test_stage_and_ingest_without_keys(tmp_path: Path):
    vault_dir = tmp_path / "vault"
    target = tmp_path / "work" / "Фото"
    target.mkdir(parents=True)
    (target / "a.txt").write_bytes(b"aaa")

    vault, _code = Vault.create(vault_dir, PASSWORD, iterations=1000)
    vault.decoys.seed_defaults()
    vault.add_folder(target, purge_traces=False, keep_folder_visible=False)
    assert not target.exists()
    vault.forget_keys()

    # папку воссоздала другая программа, пользователь бросил туда файл
    target.mkdir(parents=True)
    payload = b"fresh-shot" * 30
    dropped = target / "новый.jpg"
    dropped.write_bytes(payload)

    folder_id = vault.decoy_view.load()[0].folder_id
    assert vault.stage_new_files(folder_id, [dropped]) == 1
    assert not dropped.exists(), "файл должен исчезнуть из папки сразу"
    assert vault.staged_count() == 1
    assert list(target.iterdir()) == [], "папка снова пустая"

    # повторный вызов ничего не переносит
    assert vault.stage_new_files(folder_id, [dropped]) == 0

    out = vault.unlock(PASSWORD)
    assert out.authenticated
    assert vault.ingest_staged() == 1
    assert vault.staged_count() == 0
    vault.unlock_all()
    assert (target / "новый.jpg").read_bytes() == payload
    assert (target / "a.txt").read_bytes() == b"aaa"
    vault.forget_keys()


def test_stage_dir_is_hidden_and_inside_vault(tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault, _code = Vault.create(vault_dir, PASSWORD, iterations=1000)
    assert vault.stage_dir == vault_dir / "stage"
    assert vault.staged_count() == 0
    vault.forget_keys()

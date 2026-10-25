"""Тесты криптографического слоя и формата блоба."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vaultkeeper.core import blob as blobmod
from vaultkeeper.core import crypto
from vaultkeeper.core.errors import IntegrityError, VaultCorrupted

ITERS = 1000


def test_password_wrap_roundtrip():
    master = crypto.generate_master_key()
    wrapped = crypto.wrap_master_key(master, "пароль-1", iterations=ITERS)
    assert crypto.unwrap_master_key(wrapped, "пароль-1") == master


def test_wrong_password_returns_none_not_exception():
    master = crypto.generate_master_key()
    wrapped = crypto.wrap_master_key(master, "верный", iterations=ITERS)
    assert crypto.unwrap_master_key(wrapped, "неверный") is None


def test_password_normalization_nfkc():
    """«é» как один символ и как e+combining accent дают один ключ."""
    master = crypto.generate_master_key()
    wrapped = crypto.wrap_master_key(master, "caf\u00e9", iterations=ITERS)
    assert crypto.unwrap_master_key(wrapped, "cafe\u0301") == master


def test_seal_tamper_detected():
    key = crypto.generate_master_key()
    payload = "секрет".encode("utf-8")
    sealed = crypto.seal(payload, key, aad=b"ctx")
    assert crypto.open_sealed(sealed, key, aad=b"ctx") == payload
    broken = bytearray(sealed)
    broken[-1] ^= 0x01
    assert crypto.open_sealed(bytes(broken), key, aad=b"ctx") is None
    assert crypto.open_sealed(sealed, key, aad=b"other") is None


def test_wrapped_key_serialization():
    master = crypto.generate_master_key()
    wrapped = crypto.wrap_master_key(master, "pw", iterations=ITERS)
    restored = crypto.WrappedKey.from_bytes(wrapped.to_bytes())
    assert restored.salt == wrapped.salt
    assert restored.iterations == wrapped.iterations
    assert crypto.unwrap_master_key(restored, "pw") == master


def test_recovery_code_roundtrip():
    master = crypto.generate_master_key()
    code = crypto.generate_recovery_code()
    salt = crypto.random_bytes(32)
    blob = crypto.wrap_with_recovery(master, code, salt)
    assert crypto.unwrap_with_recovery(blob, code, salt) == master
    assert crypto.unwrap_with_recovery(blob, "AAAA-BBBB-CCCC-DDDD", salt) is None


def test_recovery_code_is_tolerant_to_case_and_spaces():
    master = crypto.generate_master_key()
    code = crypto.generate_recovery_code()
    salt = crypto.random_bytes(32)
    blob = crypto.wrap_with_recovery(master, code, salt)
    assert crypto.unwrap_with_recovery(blob, f"  {code.lower()} ", salt) == master


def test_recovery_code_alphabet_has_no_ambiguous_chars():
    for _ in range(50):
        code = crypto.generate_recovery_code()
        assert not any(ch in code for ch in "OI01")


@pytest.mark.parametrize(
    "size",
    [0, 1, 1023, 1 << 20, (1 << 20) + 7],
)
def test_blob_roundtrip(tmp_path: Path, size: int):
    src = tmp_path / "src.bin"
    src.write_bytes(os.urandom(size))
    key = crypto.generate_master_key()
    dst = tmp_path / "b.vkb"
    info = blobmod.encrypt_file(src, dst, key)
    assert info.size == size
    assert info.sha256 == crypto.sha256_file(src)

    out = tmp_path / "out.bin"
    got = blobmod.decrypt_file(dst, out, key)
    assert out.read_bytes() == src.read_bytes()
    assert got.sha256 == info.sha256


def test_blob_multichunk_roundtrip(tmp_path: Path):
    """Файл больше одного куска: проверяем счётчики nonce и склейку."""
    chunk = 64 * 1024
    src = tmp_path / "big.bin"
    data = os.urandom(chunk * 3 + 123)
    src.write_bytes(data)
    key = crypto.generate_master_key()
    dst = tmp_path / "big.vkb"
    info = blobmod.encrypt_file(src, dst, key, chunk_plain=chunk)
    assert info.chunk_count == 4
    out = tmp_path / "big.out"
    blobmod.decrypt_file(dst, out, key)
    assert out.read_bytes() == data


def test_blob_header_is_authenticated(tmp_path: Path):
    src = tmp_path / "f.txt"
    src.write_bytes(b"hello world")
    key = crypto.generate_master_key()
    dst = tmp_path / "f.vkb"
    blobmod.encrypt_file(src, dst, key)

    raw = bytearray(dst.read_bytes())
    raw[10] ^= 0xFF  # портим байт зашифрованного заголовка
    dst.write_bytes(bytes(raw))
    with pytest.raises(VaultCorrupted):
        blobmod.read_header(dst, key)


def test_blob_wrong_key_rejected(tmp_path: Path):
    src = tmp_path / "f.txt"
    src.write_bytes(b"secret")
    dst = tmp_path / "f.vkb"
    blobmod.encrypt_file(src, dst, crypto.generate_master_key())
    with pytest.raises(VaultCorrupted):
        blobmod.read_header(dst, crypto.generate_master_key())


def test_blob_body_tamper_detected(tmp_path: Path):
    src = tmp_path / "f.bin"
    src.write_bytes(os.urandom(5000))
    key = crypto.generate_master_key()
    dst = tmp_path / "f.vkb"
    blobmod.encrypt_file(src, dst, key)

    raw = bytearray(dst.read_bytes())
    raw[-5] ^= 0x01  # портим тело (тег GCM)
    dst.write_bytes(bytes(raw))
    with pytest.raises(VaultCorrupted):
        blobmod.decrypt_file(dst, tmp_path / "out.bin", key)


def test_blob_truncated_file_detected(tmp_path: Path):
    src = tmp_path / "f.bin"
    src.write_bytes(os.urandom(200_000))
    key = crypto.generate_master_key()
    dst = tmp_path / "f.vkb"
    blobmod.encrypt_file(src, dst, key)
    dst.write_bytes(dst.read_bytes()[: dst.stat().st_size // 2])
    with pytest.raises(VaultCorrupted):
        blobmod.decrypt_file(dst, tmp_path / "out.bin", key)


def test_failed_decrypt_leaves_no_partial_file(tmp_path: Path):
    src = tmp_path / "f.bin"
    src.write_bytes(os.urandom(50_000))
    key = crypto.generate_master_key()
    dst = tmp_path / "f.vkb"
    blobmod.encrypt_file(src, dst, key)
    raw = bytearray(dst.read_bytes())
    raw[-3] ^= 0x01
    dst.write_bytes(bytes(raw))
    out = tmp_path / "out.bin"
    with pytest.raises(VaultCorrupted):
        blobmod.decrypt_file(dst, out, key)
    assert not out.exists()
    assert not list(tmp_path.glob("*.vkpart"))


def test_blob_keys_differ_per_id():
    root = crypto.generate_master_key()
    assert blobmod.blob_key(root, "aaa") != blobmod.blob_key(root, "bbb")


def test_encrypt_file_is_atomic_no_part_left(tmp_path: Path):
    src = tmp_path / "f.bin"
    src.write_bytes(os.urandom(1024))
    dst = tmp_path / "sub" / "f.vkb"
    blobmod.encrypt_file(src, dst, crypto.generate_master_key())
    assert dst.exists()
    assert not list(dst.parent.glob("*.part"))


def test_integrity_error_when_size_mismatch_forced(tmp_path: Path, monkeypatch):
    src = tmp_path / "f.bin"
    src.write_bytes(b"x" * 10)
    key = crypto.generate_master_key()
    dst = tmp_path / "f.vkb"
    blobmod.encrypt_file(src, dst, key)

    real_read = blobmod.read_header

    def fake_header(path, k):
        size, sha, chunk, count = real_read(path, k)
        return size + 1, sha, chunk, count

    monkeypatch.setattr(blobmod, "read_header", fake_header)
    with pytest.raises(IntegrityError):
        blobmod.decrypt_file(dst, tmp_path / "o.bin", key)

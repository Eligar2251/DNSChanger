"""Криптографические примитивы VaultKeeper.

Схема:
    пароль --PBKDF2-HMAC-SHA256(600 000)--> KEK
    KEK  --AES-256-GCM--> распаковывает случайный master_key (32 байта)
    master_key --HKDF--> k_header / k_manifest / k_blob / k_meta

Файлы шифруются ключом, производным от k_blob и идентификатора блока,
поэтому повторное использование nonce между файлами исключено.

Смена пароля переупаковывает только master_key — данные не перешифровываются.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import unicodedata
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KEY_LEN = 32
NONCE_LEN = 12
SALT_LEN = 32
KDF_PBKDF2_SHA256 = 1

#: 600 000 итераций — рекомендация OWASP для PBKDF2-HMAC-SHA256 (2023+).
#: На обычном ПК это ~0.3 с, на перебор пароля — годы.
DEFAULT_ITERATIONS = 600_000
MIN_ITERATIONS = 100_000


class CryptoError(Exception):
    """Ошибка криптографического слоя."""


def normalize_password(password: str) -> bytes:
    """NFKC + UTF-8: одинаково выглядящие пароли дают одинаковый ключ."""
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def random_bytes(n: int) -> bytes:
    return os.urandom(n)


def derive_kek(password: str, salt: bytes, iterations: int) -> bytes:
    """Ключ шифрования ключей (KEK) из пароля."""
    if iterations < 1:
        raise CryptoError("iterations must be positive")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=KEY_LEN, salt=salt, iterations=iterations)
    return kdf.derive(normalize_password(password))


def subkey(master_key: bytes, info: bytes, salt: bytes | None = None, length: int = KEY_LEN) -> bytes:
    """Производный ключ из master_key (HKDF-SHA256)."""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(master_key)


def seal(plaintext: bytes, key: bytes, aad: bytes | None = None) -> bytes:
    """AES-256-GCM: возвращает nonce(12) || ciphertext || tag(16)."""
    nonce = random_bytes(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce + ct


def open_sealed(blob: bytes, key: bytes, aad: bytes | None = None) -> bytes | None:
    """Распаковка :func:`seal`. ``None`` — если ключ неверный или данные подделаны."""
    if len(blob) < NONCE_LEN + 16:
        return None
    nonce, ct = blob[:NONCE_LEN], blob[NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ct, aad)
    except InvalidTag:
        return None


@dataclass(frozen=True)
class WrappedKey:
    """master_key, упакованный паролем."""

    salt: bytes
    iterations: int
    blob: bytes  # nonce || AESGCM(KEK, master_key)

    def to_bytes(self) -> bytes:
        return (
            self.salt
            + self.iterations.to_bytes(4, "big")
            + len(self.blob).to_bytes(4, "big")
            + self.blob
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "WrappedKey":
        if len(data) < SALT_LEN + 8:
            raise CryptoError("wrapped key blob is too short")
        salt = data[:SALT_LEN]
        iterations = int.from_bytes(data[SALT_LEN : SALT_LEN + 4], "big")
        blob_len = int.from_bytes(data[SALT_LEN + 4 : SALT_LEN + 8], "big")
        blob = data[SALT_LEN + 8 : SALT_LEN + 8 + blob_len]
        if len(blob) != blob_len:
            raise CryptoError("wrapped key blob truncated")
        return cls(salt=salt, iterations=iterations, blob=blob)


def generate_master_key() -> bytes:
    return random_bytes(KEY_LEN)


def wrap_master_key(master_key: bytes, password: str, iterations: int = DEFAULT_ITERATIONS) -> WrappedKey:
    salt = random_bytes(SALT_LEN)
    kek = derive_kek(password, salt, iterations)
    return WrappedKey(salt=salt, iterations=iterations, blob=seal(master_key, kek, aad=b"vk-master-v1"))


def unwrap_master_key(wrapped: WrappedKey, password: str) -> bytes | None:
    """``None`` — пароль не подошёл. Именно так отличаем «свой» пароль от чужого."""
    kek = derive_kek(password, wrapped.salt, wrapped.iterations)
    return open_sealed(wrapped.blob, kek, aad=b"vk-master-v1")


# --------------------------------------------------------------------------- #
# Ключ аварийного восстановления (длинный код, который можно выписать на бумагу)
# --------------------------------------------------------------------------- #

_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без O/0/I/1


def generate_recovery_code(words: int = 12) -> str:
    """Человекочитаемый код восстановления, например ``K7QM-2XTP-...``."""
    idx = [b % len(_RECOVERY_ALPHABET) for b in os.urandom(words)]
    chars = "".join(_RECOVERY_ALPHABET[i] for i in idx)
    return "-".join(chars[i : i + 4] for i in range(0, len(chars), 4))


def recovery_code_to_key(recovery_code: str, salt: bytes, iterations: int = 200_000) -> bytes:
    """Ключ из кода восстановления. Регистр и пробелы не важны, дефисы обязательны."""
    cleaned = "".join(ch for ch in recovery_code.strip().upper() if ch in _RECOVERY_ALPHABET or ch == "-")
    if not cleaned:
        raise CryptoError("empty recovery code")
    return derive_kek(cleaned, salt, iterations)


def wrap_with_recovery(master_key: bytes, recovery_code: str, salt: bytes) -> bytes:
    key = recovery_code_to_key(recovery_code, salt)
    return seal(master_key, key, aad=b"vk-recovery-v1")


def unwrap_with_recovery(blob: bytes, recovery_code: str, salt: bytes) -> bytes | None:
    key = recovery_code_to_key(recovery_code, salt)
    return open_sealed(blob, key, aad=b"vk-recovery-v1")


# --------------------------------------------------------------------------- #
# Удобные мелочи
# --------------------------------------------------------------------------- #


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def constant_time_eq(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


def short_id(n: int = 12) -> str:
    return base64.urlsafe_b64encode(os.urandom(n)).decode("ascii").rstrip("=")

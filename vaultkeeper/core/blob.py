"""Потоковое шифрование отдельных файлов (блобов) кусками по 4 МиБ.

Формат файла-блоба ``*.vkb``::

    "VKB1" | ver u8 | flags u8 | pad u16
    hdr_nonce(12) | hdr_len u32 | hdr_ct            # зашифрованный заголовок
    ( nonce(12) | ct_len u32 | ct ) * chunk_count   # ct уже содержит GCM-тег

Зашифрованный заголовок содержит::

    orig_size u64 | sha256(32) | chunk_plain u32 | chunk_count u32

Заголовок аутентифицирован, поэтому подменить размер или контрольную сумму
нельзя: любой tampering даст ошибку расшифровки.

Nonce каждого куска = случайный 8-байтный префикс файла + 4-байтный счётчик,
то есть повторы nonce исключены даже для одинаковых данных.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import NONCE_LEN, random_bytes, subkey
from .errors import IntegrityError, VaultCorrupted

MAGIC = b"VKB1"
VERSION = 1
FLAG_NONE = 0

CHUNK_PLAIN = 4 * 1024 * 1024  # 4 МиБ
_HDR_STRUCT = struct.Struct(">Q32sII")  # size, sha256, chunk_plain, chunk_count

# magic(4) + ver/flags/pad(4) + nonce(12) + hdr_len(4)
_FIXED_HEADER_LEN = 4 + 4 + NONCE_LEN + 4
# заголовок всегда одной длины: 48 байт данных + 16 байт GCM-тега
_HDR_CT_LEN = _HDR_STRUCT.size + 16
#: суммарная длина «шапки» блоба; тело всегда начинается ровно отсюда
HEADER_LEN = _FIXED_HEADER_LEN + _HDR_CT_LEN


@dataclass(frozen=True)
class BlobInfo:
    """Что получилось после шифрования файла."""

    size: int
    sha256: str
    chunk_count: int
    encrypted_size: int


def blob_key(master_blob_key: bytes, blob_id: str) -> bytes:
    """Индивидуальный ключ блока: nonce-пространства файлов не пересекаются."""
    return subkey(master_blob_key, info=b"vk-blob-v1", salt=blob_id.encode("utf-8"))


def _chunk_nonce(prefix: bytes, index: int) -> bytes:
    return prefix + struct.pack(">I", index)


def encrypt_file(src: Path | str, dst: Path | str, key: bytes, chunk_plain: int = CHUNK_PLAIN) -> BlobInfo:
    """Шифрует ``src`` в ``dst`` одним проходом, считая sha256 открытого текста.

    ``dst`` пишется во временный файл и атомарно переименовывается, поэтому
    прерванный процесс никогда не оставляет «половину» блоба под рабочим именем.
    """
    src, dst = Path(src), Path(dst)
    if chunk_plain <= 0 or chunk_plain > 64 * 1024 * 1024:
        raise ValueError("chunk_plain out of range")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")

    aes = AESGCM(key)
    prefix = random_bytes(8)
    hasher = hashlib.sha256()
    total = 0
    chunks = 0

    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        # Тело пишется со смещения HEADER_LEN: «шапка» дописывается в конце,
        # когда известны размер и sha256 открытого текста.
        fout.seek(HEADER_LEN)

        while True:
            block = fin.read(chunk_plain)
            if not block:
                break
            hasher.update(block)
            total += len(block)
            nonce = _chunk_nonce(prefix, chunks)
            ct = aes.encrypt(nonce, block, None)
            fout.write(nonce)
            fout.write(struct.pack(">I", len(ct)))
            fout.write(ct)
            chunks += 1

        digest = hasher.digest()
        hdr_plain = _HDR_STRUCT.pack(total, digest, chunk_plain, chunks)
        hdr_nonce = random_bytes(NONCE_LEN)
        hdr_ct = aes.encrypt(hdr_nonce, hdr_plain, MAGIC)
        if len(hdr_ct) != _HDR_CT_LEN:  # pragma: no cover - защита формата
            raise VaultCorrupted("internal error: unexpected header length")

        fout.seek(0)
        fout.write(MAGIC)
        fout.write(struct.pack(">BBH", VERSION, FLAG_NONE, 0))
        fout.write(hdr_nonce)
        fout.write(struct.pack(">I", len(hdr_ct)))
        fout.write(hdr_ct)
        if fout.tell() != HEADER_LEN:  # pragma: no cover - защита формата
            raise VaultCorrupted("internal error: header/body boundary mismatch")
        fout.flush()
        os.fsync(fout.fileno())

    tmp.replace(dst)
    return BlobInfo(size=total, sha256=digest.hex(), chunk_count=chunks, encrypted_size=dst.stat().st_size)


def read_header(path: Path | str, key: bytes) -> tuple[int, str, int, int]:
    """Возвращает (size, sha256_hex, chunk_plain, chunk_count) без расшифровки тела."""
    with open(path, "rb") as fh:
        magic = fh.read(4)
        if magic != MAGIC:
            raise VaultCorrupted(f"{path}: это не файл хранилища VaultKeeper")
        ver, _flags, _pad = struct.unpack(">BBH", fh.read(4))
        if ver != VERSION:
            raise VaultCorrupted(f"{path}: неподдерживаемая версия блоба {ver}")
        hdr_nonce = fh.read(NONCE_LEN)
        (hdr_len,) = struct.unpack(">I", fh.read(4))
        if hdr_len <= 0 or hdr_len > 4096:
            raise VaultCorrupted(f"{path}: повреждённая длина заголовка")
        hdr_ct = fh.read(hdr_len)
        if len(hdr_ct) != hdr_len:
            raise VaultCorrupted(f"{path}: заголовок оборван")
        try:
            hdr_plain = AESGCM(key).decrypt(hdr_nonce, hdr_ct, MAGIC)
        except InvalidTag as exc:
            raise VaultCorrupted(f"{path}: неверный ключ или файл изменён") from exc
    size, digest, chunk_plain, chunk_count = _HDR_STRUCT.unpack(hdr_plain)
    return size, digest.hex(), chunk_plain, chunk_count


def decrypt_file(src: Path | str, dst: Path | str, key: bytes, verify: bool = True) -> BlobInfo:
    """Расшифровывает блоб в ``dst`` и проверяет контрольную сумму.

    При любом расхождении временный файл удаляется и поднимается
    :class:`IntegrityError` — исходные данные при этом не тронуты.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".vkpart")

    size, sha_hex, chunk_plain, chunk_count = read_header(src, key)
    aes = AESGCM(key)
    hasher = hashlib.sha256()
    written = 0
    index = 0

    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        fin.read(4 + 4)  # magic + ver/flags/pad
        hdr_nonce = fin.read(NONCE_LEN)
        (hdr_len,) = struct.unpack(">I", fin.read(4))
        fin.read(hdr_len)
        # префикс nonce восстанавливать не нужно: он записан в каждом куске
        while index < chunk_count:
            raw_nonce = fin.read(NONCE_LEN)
            if len(raw_nonce) != NONCE_LEN:
                _discard(tmp)
                raise VaultCorrupted(f"{src}: блоб оборван на куске {index}")
            (ct_len,) = struct.unpack(">I", fin.read(4))
            ct = fin.read(ct_len)
            if len(ct) != ct_len:
                _discard(tmp)
                raise VaultCorrupted(f"{src}: блоб оборван в данных куска {index}")
            try:
                plain = aes.decrypt(raw_nonce, ct, None)
            except InvalidTag as exc:
                _discard(tmp)
                raise VaultCorrupted(f"{src}: кусок {index} повреждён") from exc
            if raw_nonce[8:] != struct.pack(">I", index):
                _discard(tmp)
                raise VaultCorrupted(f"{src}: нарушен порядок кусков")
            fout.write(plain)
            hasher.update(plain)
            written += len(plain)
            index += 1

        if written != size:
            _discard(tmp)
            raise IntegrityError(f"{src}: размер не совпал ({written} != {size})")
        if verify and hasher.hexdigest() != sha_hex:
            _discard(tmp)
            raise IntegrityError(f"{src}: контрольная сумма не совпала")
        fout.flush()
        os.fsync(fout.fileno())

    tmp.replace(dst)
    return BlobInfo(size=written, sha256=hasher.hexdigest(), chunk_count=index, encrypted_size=src.stat().st_size)


def _discard(tmp: Path) -> None:
    try:
        tmp.unlink()
    except OSError:  # pragma: no cover
        pass

"""Общие фикстуры: быстрое хранилище во временной папке."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vaultkeeper.core.vault import Vault  # noqa: E402
from vaultkeeper.core.win import EmergencyKeyStore  # noqa: E402

TEST_ITERATIONS = 1000
PASSWORD = "correct horse battery staple"


class MemoryEmergencyStore(EmergencyKeyStore):
    """Замена DPAPI для тестов: хранит ключ «как Windows», но в памяти."""

    available = True

    def __init__(self) -> None:
        self._data: bytes | None = None

    def save(self, master_key: bytes) -> None:
        self._data = master_key

    def load(self) -> bytes:
        from vaultkeeper.core.errors import EmergencyUnavailable

        if self._data is None:
            raise EmergencyUnavailable("ключа нет")
        return self._data

    def forget(self) -> None:
        self._data = None

    def has_key(self) -> bool:
        return self._data is not None


@pytest.fixture()
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


@pytest.fixture()
def emergency() -> MemoryEmergencyStore:
    return MemoryEmergencyStore()


@pytest.fixture()
def vault(vault_root: Path, emergency: MemoryEmergencyStore) -> Vault:
    v, code = Vault.create(vault_root, PASSWORD, iterations=TEST_ITERATIONS, emergency_store=emergency)
    v.recovery_code = code  # type: ignore[attr-defined]
    v.forget_keys()
    outcome = v.unlock(PASSWORD)
    assert outcome.authenticated
    return v


def make_tree(root: Path, spec: dict[str, str | int | bytes]) -> None:
    """spec: {"a/b.txt": "content", "a/b.bin": b"bytes", "big.bin": 1024 (случайные байты)}"""
    for rel, content in spec.items():
        p = root / Path(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, int):
            p.write_bytes(os.urandom(content))
        elif isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_bytes(content.encode("utf-8"))

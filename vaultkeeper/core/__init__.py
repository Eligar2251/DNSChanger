"""Ядро VaultKeeper: криптография, хранилище, файловые операции.

Модули ядра НЕ импортируют Qt — их можно тестировать и использовать из CLI.
"""

from __future__ import annotations

__all__ = ["blob", "crypto", "decoys", "errors", "fsutil", "manifest", "traces", "vault", "win"]

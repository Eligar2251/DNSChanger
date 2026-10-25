"""VaultKeeper — сейф для папок в Windows.

Файлы physically переносятся в зашифрованный контейнер AES-256-GCM,
а на месте папок остаются пустые каталоги с файлами-приманками.
"""

from __future__ import annotations

__version__ = "1.0.0"
APP_NAME = "VaultKeeper"
APP_NAME_RU = "Сейф"
__all__ = ["__version__", "APP_NAME", "APP_NAME_RU"]

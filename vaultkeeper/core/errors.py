"""Исключения VaultKeeper.

Все ошибки, которые может увидеть интерфейс, наследуются от :class:`VaultError`,
чтобы UI мог показывать их одним обработчиком.
"""

from __future__ import annotations


class VaultError(Exception):
    """Базовая ошибка приложения."""


class VaultNotFound(VaultError):
    """Хранилище ещё не создано."""


class VaultCorrupted(VaultError):
    """Файл хранилища повреждён или подделан."""


class WrongPassword(VaultError):
    """Пароль не подошёл.

    Внимание: интерфейс намеренно НЕ показывает эту ошибку пользователю —
    вместо неё открывается набор папок-обманок.
    """


class FolderError(VaultError):
    """Проблема с защищаемой папкой (нет доступа, уже защищена и т. п.)."""


class IntegrityError(VaultError):
    """Контрольная сумма не совпала — операция остановлена до удаления данных."""


class OperationInterrupted(VaultError):
    """Предыдущая операция была прервана (например, процесс убили)."""


class EmergencyUnavailable(VaultError):
    """Аварийный ключ недоступен (другой пользователь Windows / другая машина)."""

# jsonwebtoken/exceptions/utils.py

from typing import Optional
from exceptions.base import BaseAppException


class AuthDomainException(BaseAppException):
    """Базовое исключение для всех сценариев аутентификации и авторизации."""

    error_code = "AUTH_DOMAIN_ERROR"


class CryptoKeyNotFoundError(AuthDomainException):
    """Выбрасывается, когда на сервере физически отсутствуют файлы ключей JWT (.pem)."""

    status_code = 500
    error_code = "CRYPTO_KEY_NOT_FOUND"
    message = "Критическая ошибка конфигурации сервера: ключ шифрования не найден."


class InvalidTokenError(AuthDomainException):
    """Выбрасывается при протухшем, испорченном токене или неверном типе токена."""

    status_code = 401
    error_code = "INVALID_TOKEN"
    message = "Предоставленный токен невалиден или истек."

    def __init__(
        self, detail_message: Optional[str] = None, reason: Optional[str] = None
    ):
        message = detail_message or self.message
        details = {"reason": reason} if reason else {}
        super().__init__(message=message, details=details)


class UserAccountNotFoundError(AuthDomainException):
    """Выбрасывается, если токен валиден, но пользователь из 'sub' удален или отсутствует в БД."""

    status_code = 401
    error_code = "USER_ACCOUNT_NOT_FOUND"
    message = "Учетная запись пользователя не найдена в системе."


class InsufficientPermissionsError(AuthDomainException):
    """Выбрасывается при нарушении ролевой модели (RBAC)."""

    status_code = 403
    error_code = "INSUFFICIENT_PERMISSIONS"
    message = "Недостаточно прав для выполнения операции с данным ресурсом."


class InstanceAssociationError(AuthDomainException):
    """Выбрасывается, если у создателя (CREATOR) отсутствует привязка к Docker-инстансу / CRM-клиенту."""

    status_code = 400
    error_code = "INSTANCE_ASSOCIATION_REQUIRED"
    message = "Аккаунт создателя не ассоциирован ни с одним активным инстансом системы."

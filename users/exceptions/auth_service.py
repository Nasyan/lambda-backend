# users/exceptions/auth_service.py

from typing import Any, Optional
from exceptions.base import BaseAppException


class AuthDomainException(BaseAppException):
    """Базовое исключение для всех сбоев в подсистеме аутентификации и регистрации."""

    error_code = "AUTH_DOMAIN_ERROR"


# --- Rate Limiting & Anti-Spam ---
class AuthRateLimitExceededError(AuthDomainException):
    """Выбрасывается при нарушении анти-спам лимита на отправку кодов."""

    status_code = 429
    error_code = "AUTH_RATE_LIMIT_EXCEEDED"
    message = "Слишком много запросов. Пожалуйста, подождите."

    def __init__(self, email: str, ttl_remaining: int):
        super().__init__(
            message=f"{self.message} Повторная отправка доступна через {ttl_remaining} сек.",
            details={"email": email, "ttl_remaining": ttl_remaining},
        )


# --- Invitations ---
class InvitationRequiredError(AuthDomainException):
    """Выбрасывается, если пользователь пытается зарегистрироваться без инвайта."""

    status_code = 403
    error_code = "INVITATION_REQUIRED"
    message = "Регистрация недоступна. Вы должны получить приглашение от администратора или создателя контента."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class InvitationExpiredError(AuthDomainException):
    """Выбрасывается, если инвайт протух или был отозван при повторной отправке кода."""

    status_code = 403
    error_code = "INVITATION_EXPIRED"
    message = "Ссылка-приглашение истекла или была аннулирована."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


# --- Verification Codes ---
class VerificationCodeExpiredError(AuthDomainException):
    """Выбрасывается, если код подтверждения устарел (нет записи в Redis)."""

    status_code = 400
    error_code = "VERIFICATION_CODE_EXPIRED"
    message = "Код подтверждения устарел или не запрашивался."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class InvalidVerificationCodeError(AuthDomainException):
    """Выбрасывается при вводе неверного кода подтверждения."""

    status_code = 400
    error_code = "INVALID_VERIFICATION_CODE"
    message = "Введен неверный код подтверждения."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


# --- Credentials & Tokens ---
class UserAlreadyRegisteredError(AuthDomainException):
    """Выбрасывается, если пользователь уже зарегистрирован и подтвержден."""

    status_code = 400
    error_code = "USER_ALREADY_REGISTERED"
    message = "Пользователь с таким email уже зарегистрирован и активен."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class UserNotFoundError(AuthDomainException):
    """Выбрасывается, когда пользователь не найден в БД при верификации/повторной отправке."""

    status_code = 404
    error_code = "USER_NOT_FOUND"
    message = "Пользователь с таким email-адресом не найден в системе."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class InvalidCredentialsError(AuthDomainException):
    """Выбрасывается при неверном email или пароле для входа."""

    status_code = 400
    error_code = "INVALID_CREDENTIALS"
    message = "Неверный email или пароль."

    def __init__(self):
        super().__init__(message=self.message)


class InvalidTokenCredentialsError(AuthDomainException):
    """Аналог HTTP_401 для JWT-сессий, когда токен протух или подделан."""

    status_code = 401
    error_code = "INVALID_TOKEN_CREDENTIALS"
    message = "Не удалось подтвердить подлинность сессии."

    def __init__(self, reason: Optional[str] = None):
        super().__init__(
            message=self.message,
            details={"reason": reason or "Token is invalid or expired"},
        )


# --- Integrity (Internal System Errors) ---
class StorageDataCorruptedError(AuthDomainException):
    """Выбрасывается, если структура данных в Redis повреждена (500 ошибка)."""

    status_code = 500
    error_code = "STORAGE_DATA_CORRUPTED"
    message = "Внутренняя ошибка целостности данных сессии регистрации."

    def __init__(self, key: str, raw_value: Any):
        super().__init__(
            message=self.message,
            details={"redis_key": key, "raw_value": str(raw_value)},
        )

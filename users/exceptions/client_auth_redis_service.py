# users/exceptions/client_auth_redis_service.py

from exceptions.base import BaseAppException


class ClientAuthDomainException(BaseAppException):
    """Базовое исключение для всех сбоев в подсистеме публичной регистрации клиентов."""

    error_code = "CLIENT_AUTH_DOMAIN_ERROR"


class ClientAuthRateLimitExceededError(ClientAuthDomainException):
    """Выбрасывается при нарушении анти-спам лимита на отправку кодов публичной формы."""

    error_code = "CLIENT_AUTH_RATE_LIMIT_EXCEEDED"
    message = "Слишком много запросов. Пожалуйста, подождите."

    def __init__(self, email: str, ttl_remaining: int):
        super().__init__(
            message=f"{self.message} Повторный запрос кода доступен через {ttl_remaining} сек.",
            details={"email": email, "ttl_remaining": ttl_remaining},
        )


class ClientVerificationCodeExpiredError(ClientAuthDomainException):
    """Выбрасывается, если код подтверждения публичного пользователя устарел или не запрашивался."""

    error_code = "CLIENT_VERIFICATION_CODE_EXPIRED"
    message = "Код подтверждения устарел или не запрашивался."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class ClientInvalidVerificationCodeError(ClientAuthDomainException):
    """Выбрасывается при вводе неверного кода подтверждения клиентом."""

    error_code = "CLIENT_INVALID_VERIFICATION_CODE"
    message = "Введен неверный код подтверждения."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})

# users/exceptions/client_auth_service.py

from uuid import UUID
from exceptions.base import BaseAppException


class ClientServiceDomainException(BaseAppException):
    """Базовое исключение для бизнес-логики клиентского сервиса аутентификации."""

    error_code = "CLIENT_SERVICE_DOMAIN_ERROR"


class StorefrontInstanceNotFoundError(ClientServiceDomainException):
    """Выбрасывается, если инстанс магазина/витрины не существует."""

    status_code = 404
    error_code = "STOREFRONT_INSTANCE_NOT_FOUND"
    message = "Запрошенная витрина магазина не найдена."

    def __init__(self, instance_id: UUID):
        super().__init__(
            message=self.message, details={"instance_id": str(instance_id)}
        )


class ClientAlreadyRegisteredError(ClientServiceDomainException):
    """Выбрасывается, если клиент с таким email уже активен в системе."""

    status_code = 400
    error_code = "CLIENT_ALREADY_REGISTERED"
    message = "Пользователь с таким email-адресом уже зарегистрирован."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class ClientNotFoundError(ClientServiceDomainException):
    """Выбрасывается, если запись пользователя отсутствует при верификации."""

    status_code = 404
    error_code = "CLIENT_NOT_FOUND"
    message = "Профиль клиента не найден."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class InvalidResendRequestError(ClientServiceDomainException):
    """Выбрасывается, если код запрашивается повторно для несуществующего или уже активного аккаунта."""

    status_code = 400
    error_code = "INVALID_RESEND_REQUEST"
    message = "Некорректный запрос повторной отправки кода (аккаунт активен или не зарегистрирован)."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class InvalidClientCredentialsError(ClientServiceDomainException):
    """Выбрасывается при неверной паре логин/пароль или попытке обойти ограничения ролей."""

    status_code = 400
    error_code = "INVALID_CLIENT_CREDENTIALS"
    message = "Неверный email или пароль."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class InvalidClientTokenSessionError(ClientServiceDomainException):
    """Выбрасывается, если refresh-токен клиента не валиден, протух или роль скомпрометирована."""

    status_code = 401
    error_code = "INVALID_CLIENT_TOKEN_SESSION"
    message = "Сессия авторизации клиента недействительна или истекла."

    def __init__(self, reason: str):
        super().__init__(message=self.message, details={"reason": reason})

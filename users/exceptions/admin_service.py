# users/exceptions/admin_service.py

from uuid import UUID
from exceptions.base import BaseAppException


class UsersDomainException(BaseAppException):
    """Базовое исключение для домена пользователей и административного управления."""

    error_code = "USERS_DOMAIN_ERROR"


# --- Ошибки инстансов (пространств) ---


class InstanceNotFoundError(UsersDomainException):
    """Выбрасывается, если запрашиваемый инстанс не найден."""

    status_code = 404
    error_code = "INSTANCE_NOT_FOUND"
    message = "Запрошенное пространство (инстанс) не найдено."

    def __init__(self, instance_id: UUID):
        super().__init__(
            message=self.message, details={"instance_id": str(instance_id)}
        )


class InstanceAlreadyExistsError(UsersDomainException):
    """Выбрасывается при попытке создать инстанс с уже занятым именем/тайтлом."""

    status_code = 400
    error_code = "INSTANCE_ALREADY_EXISTS"
    message = "Пространство с таким названием уже существует."

    def __init__(self, title: str):
        super().__init__(message=self.message, details={"title": title})


class InstanceDeactivatedError(UsersDomainException):
    """Выбрасывается при попытке совершить действие со скрытым/отключенным инстансом."""

    status_code = 400
    error_code = "INSTANCE_DEACTIVATED"
    message = "Целевое пространство деактивировано и недоступно для операций."

    def __init__(self, instance_id: UUID):
        super().__init__(
            message=self.message, details={"instance_id": str(instance_id)}
        )


# --- Ошибки пользователей и авторизации ---


class CreatorNotFoundError(UsersDomainException):
    """Выбрасывается, если создатель (Creator) не найден или роль не совпадает."""

    status_code = 404
    error_code = "CREATOR_NOT_FOUND"
    message = "Создатель (Creator) с указанным идентификатором не найден."

    def __init__(self, creator_uuid: UUID):
        super().__init__(
            message=self.message, details={"creator_uuid": str(creator_uuid)}
        )


class UserAlreadyExistsError(UsersDomainException):
    """Выбрасывается, если email уже занят в системе."""

    status_code = 400
    error_code = "USER_ALREADY_EXISTS"
    message = "Пользователь с таким email-адресом уже зарегистрирован."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class CreatorAlreadyDeactivatedError(UsersDomainException):
    """Выбрасывается при повторной попытке деактивировать уже отключенного создателя."""

    status_code = 400
    error_code = "CREATOR_ALREADY_DEACTIVATED"
    message = "Профиль этого создателя уже был деактивирован ранее."

    def __init__(self, creator_uuid: UUID):
        super().__init__(
            message=self.message, details={"creator_uuid": str(creator_uuid)}
        )


class InvalidAdminCredentialsError(UsersDomainException):
    """Выбрасывается при неверном логине/пароле администратора или отсутствии прав."""

    status_code = 401
    error_code = "INVALID_ADMIN_CREDENTIALS"
    message = "Неверные учетные данные или недостаточно прав для доступа к панели администратора."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class UserNotFoundError(Exception):
    status_code = 400
    error_code = "INVALID_ADMIN_CREDENTIALS"
    message = "Неверные учетные данные или недостаточно прав для доступа к панели администратора."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})

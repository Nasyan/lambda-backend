# mongo/exceptions/record.py

from typing import Any, Optional
from exceptions.base import BaseAppException


class RecordDomainException(BaseAppException):
    """Базовое исключение для всех операций со строками динамических таблиц в MongoDB."""

    error_code = "RECORD_DOMAIN_ERROR"


class RecordNotFoundError(RecordDomainException):
    """Выбрасывается, когда конкретная строка таблицы (Record) не найдена по UUID."""

    error_code = "RECORD_NOT_FOUND"
    message = "Запрашиваемая запись не найдена."

    def __init__(
        self, record_uuid: str, instance_uuid: str, message: Optional[str] = None
    ):
        details = {"record_uuid": record_uuid, "instance_uuid": instance_uuid}
        super().__init__(message=message or self.message, details=details)


class RecordValidationError(RecordDomainException):
    """Выбрасывается при нарушении схемы данных, типов, ограничений или обязательных полей."""

    error_code = "RECORD_VALIDATION_FAILED"
    message = "Ошибка валидации данных записи."

    def __init__(
        self,
        message: str,
        field_name: Optional[str] = None,
        invalid_value: Optional[Any] = None,
        reason: Optional[str] = None,
    ):
        details = {}
        if field_name:
            details["field_name"] = field_name
        if invalid_value:
            details["invalid_value"] = invalid_value
        if reason:
            details["reason"] = reason

        super().__init__(message=message, details=details)


class DuplicateRecordKeyError(RecordValidationError):
    """Выбрасывается, когда нарушено динамическое бизнес-ограничение unique=True."""

    error_code = "DUPLICATE_RECORD_KEY"
    message = "Значение поля должно быть уникальным в рамках текущей таблицы."

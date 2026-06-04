# core/exceptions/record.py

from typing import Any, Optional
from exceptions.base import BaseAppException


class RecordDomainException(BaseAppException):
    """Базовое исключение для всех бизнес-ошибок домена динамических записей и шаблонов."""

    error_code = "RECORD_DOMAIN_ERROR"
    message = "Ошибка при работе с данными таблиц."


class TemplateNotFoundDomainError(RecordDomainException):
    """Выбрасывается, когда запрашиваемый low-code шаблон (структура таблицы) не найден."""

    error_code = "TEMPLATE_NOT_FOUND"
    message = "Запрашиваемая структура таблицы (шаблон) не найдена или доступ к ней ограничен."

    def __init__(self, template_uuid: Any, instance_uuid: Optional[Any] = None):
        details = {"template_uuid": str(template_uuid)}
        if instance_uuid:
            details["instance_uuid"] = str(instance_uuid)
        super().__init__(details=details)


class RecordNotFoundDomainError(RecordDomainException):
    """Выбрасывается, когда конкретная строка/запись в динамической таблице не найдена."""

    error_code = "RECORD_NOT_FOUND"
    message = "Запись в таблице не найдена или доступ к ней ограничен."

    def __init__(self, record_uuid: Any, instance_uuid: Optional[Any] = None):
        details = {"record_uuid": str(record_uuid)}
        if instance_uuid:
            details["instance_uuid"] = str(instance_uuid)
        super().__init__(details=details)


class RecordValidationError(RecordDomainException):
    """Выбрасывается при нарушении целостности данных или схемы при сохранении/обновлении записи."""

    error_code = "RECORD_VALIDATION_ERROR"

    # Конструктор сам инкапсулирует логику сборки message и details!
    def __init__(
        self,
        action: str,  # "создании" или "обновлении"
        error: Exception,
        template_uuid: str,
        record_uuid: Optional[str] = None,
    ):
        message = f"Ошибка валидации данных при {action} записи: {str(error)}"

        details = {
            "raw_database_error": str(error),
            "template_uuid": template_uuid,
        }
        if record_uuid:
            details["record_uuid"] = record_uuid

        super().__init__(message=message, details=details)

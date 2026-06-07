# mongo/exceptions/template.py

from typing import Any, Dict, Optional
from exceptions.base import BaseAppException


class TemplateDomainException(BaseAppException):
    """Базовое исключение для всех операций со схемами и шаблонами таблиц в MongoDB."""

    error_code = "TEMPLATE_DOMAIN_ERROR"


class TemplateNotFoundError(TemplateDomainException):
    """Выбрасывается, когда шаблон (структура динамической таблицы) не найден."""

    error_code = "TEMPLATE_NOT_FOUND"
    message = "Запрашиваемый шаблон таблицы не найден."

    def __init__(
        self,
        template_uuid: str,
        instance_uuid: Optional[str] = None,
        message: Optional[str] = None,
    ):
        details = {"template_uuid": template_uuid}
        if instance_uuid:
            details["instance_uuid"] = instance_uuid
        super().__init__(message=message or self.message, details=details)


class SchemaMutationError(TemplateDomainException):
    """Выбрасывается при невозможности изменить структуру таблицы (миграция метаданных заблокирована данными)."""

    error_code = "SCHEMA_MUTATION_FAILED"
    message = "Не удалось изменить структуру таблицы из-за несовместимости с существующими данными."

    def __init__(
        self,
        template_uuid: str,
        column_name: str,
        message: str,
        reason: Optional[str] = None,
    ):
        details = {
            "template_uuid": template_uuid,
            "column_name": column_name,
            "reason": reason or "backward_compatibility_violation",
        }
        super().__init__(message=message, details=details)


class TemplateValidationError(TemplateDomainException):
    """Выбрасывается, если само описание схемы (типы колонок, маски) составлено некорректно."""

    error_code = "TEMPLATE_VALIDATION_FAILED"
    message = "Некорректная структура описания схемы данных шаблона."

    def __init__(
        self,
        message: Optional[str] = None,
        column_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Универсальный конструктор, который позволяет передать как просто сообщение,
        так и конкретное имя колонки, чтобы тесты могли его легко распарсить.
        """
        err_details = details or {}

        if column_name:
            err_details["column_name"] = column_name
            # Если передали имя колонки, автоматически улучшаем сообщение для логов/тестов
            fallback_message = f"{self.message} Ошибка в колонке: '{column_name}'."
        else:
            fallback_message = self.message

        super().__init__(message=message or fallback_message, details=err_details)

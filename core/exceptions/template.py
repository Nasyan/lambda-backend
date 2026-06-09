# core/exceptions/template.py

from typing import Any, Optional
from exceptions.base import BaseAppException


class TemplateDomainException(BaseAppException):
    """Базовое исключение для ошибок управления схемами и шаблонами таблиц."""

    error_code = "TEMPLATE_DOMAIN_ERROR"


class TemplateNotFoundException(TemplateDomainException):
    """Выбрасывается, когда запрашиваемый шаблон (low-code таблица) не найден."""

    status_code = 404
    error_code = "TEMPLATE_NOT_FOUND"
    message = "Запрашиваемый шаблон таблицы не найден."

    def __init__(self, template_uuid: Any, instance_uuid: Any):
        details = {
            "template_uuid": str(template_uuid),
            "instance_uuid": str(instance_uuid),
        }
        super().__init__(message=self.message, details=details)


class TemplateMutationError(TemplateDomainException):
    """Выбрасывается, когда изменение структуры (удаление/изменение колонки) небезопасно или нарушает схему."""

    status_code = 422
    error_code = "TEMPLATE_MUTATION_ERROR"

    def __init__(
        self,
        action: str,
        error: Exception,
        template_uuid: Any,
        column_name: Optional[str] = None,
    ):
        message = f"Не удалось выполнить {action} для шаблона: {str(error)}"
        details = {"raw_error": str(error), "template_uuid": str(template_uuid)}
        if column_name:
            details["column_name"] = column_name

        super().__init__(message=message, details=details)


class DuplicateTemplateNameException(TemplateDomainException):
    """Выбрасывается, когда пользователь пытается создать шаблон с именем, которое уже занято в текущем инстансе."""

    status_code = 409
    error_code = "TEMPLATE_NAME_ALREADY_EXISTS"
    message = "Таблица с таким именем уже существует в данном пространстве."

    def __init__(self, name: str, instance_uuid: Any):
        details = {
            "conflicting_name": name,
            "instance_uuid": str(instance_uuid),
        }
        # Переопределяем дефолтное сообщение, делая его более информативным для фронтенда/клиента
        custom_message = f"Невозможно создать таблицу '{name}'. {self.message}"
        super().__init__(message=custom_message, details=details)

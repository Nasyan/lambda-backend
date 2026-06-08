# engine/exceptions/integrity.py

from typing import Any, Dict, List, Optional
from exceptions.base import BaseAppException


class IntegrityDomainException(BaseAppException):
    """Базовое исключение для ошибок целостности метаданных low-code схем."""

    error_code = "INTEGRITY_DOMAIN_ERROR"


class CircularDependencyError(IntegrityDomainException):
    """Выбрасывается при обнаружении циклических зависимостей в графе формул."""

    status_code = 400
    error_code = "CIRCULAR_DEPENDENCY_ERROR"
    message = "Обнаружена циклическая зависимость в расчетных формулах."

    def __init__(self, message: Optional[str] = None):
        super().__init__(message=message or self.message)


class SchemaValidationError(IntegrityDomainException):
    """Выбрасывается при невалидных конфигурациях (несуществующие поля в масках/фильтрах)."""

    status_code = 400
    error_code = "SCHEMA_VALIDATION_ERROR"

    def __init__(self, reason: str, invalid_fields: List[str], target_context: str):
        message = f"Ошибка конфигурации {target_context}: полей {invalid_fields} не существует."
        details = {
            "reason": reason,
            "invalid_fields": invalid_fields,
            "context": target_context,
        }
        super().__init__(message=message, details=details)


class SchemaDependencyError(IntegrityDomainException):
    """Выбрасывается, когда деструктивное изменение (удаление таблицы или поля)
    блокируется из-за существующих внешних зависимостей платформы."""

    status_code = 409
    error_code = "SCHEMA_DEPENDENCY_CONFLICT"

    def __init__(
        self,
        message: str,
        target_resource: str,
        conflicts: List[str],
        raw_details: Optional[Dict[str, Any]] = None,
    ):
        details = {
            "target_resource": target_resource,
            "conflicts_summary": conflicts,
            **(raw_details or {}),
        }
        super().__init__(message=message, details=details)

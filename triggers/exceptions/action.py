# triggers/exceptions/action.py

from typing import Any, Dict, Optional
from exceptions.base import BaseAppException


class AutomationDomainException(BaseAppException):
    """Базовое исключение для всех сбоев в подсистеме триггеров и автоматизаций."""

    error_code = "AUTOMATION_DOMAIN_ERROR"


class AutomationValidationError(AutomationDomainException):
    """Выбрасывается при ошибках валидации структуры, параметров No-Code экшена или AST графа."""

    error_code = "AUTOMATION_VALIDATION_FAILED"
    message = "Ошибка валидации параметров автоматизации."

    def __init__(
        self,
        action_name: Optional[str] = None,
        instance_uuid: Optional[str] = None,
        reason: Optional[str] = None,
        detail: Optional[str] = None,  # Добавили для простых текстовых ошибок
        details: Optional[Dict[str, Any]] = None,
    ):
        extended_details = details or {}
        if action_name:
            extended_details["action_name"] = action_name
        if instance_uuid:
            extended_details["instance_uuid"] = instance_uuid
        if reason:
            extended_details["reason"] = reason

        # Определяем итоговый текст ошибки
        if detail:
            msg = detail
        elif action_name and reason:
            msg = f"{self.message} Экшен: {action_name}. Причина: {reason}"
        else:
            msg = self.message

        super().__init__(message=msg, details=extended_details)


class TriggerNotFoundDomainError(AutomationDomainException):
    """Выбрасывается, когда запрашиваемый триггер отсутствует в базе данных инстанса."""

    error_code = "TRIGGER_NOT_FOUND"
    message = "Запрашиваемый триггер автоматизации не найден."

    def __init__(self, trigger_uuid: str, instance_uuid: Optional[str] = None):
        details = {"trigger_uuid": trigger_uuid}
        if instance_uuid:
            details["instance_uuid"] = instance_uuid

        msg = f"{self.message} ID триггера: {trigger_uuid}"
        super().__init__(message=msg, details=details)


class AutomationExecutionError(AutomationDomainException):
    """Выбрасывается при критических сбоях во время выполнения (например, в рантайме экшена)."""

    error_code = "AUTOMATION_EXECUTION_FAILED"
    message = "Критическая ошибка при исполнении экшена автоматизации."

    def __init__(
        self,
        action_name: Optional[str] = None,
        instance_uuid: Optional[str] = None,
        reason: Optional[str] = None,
        detail: Optional[str] = None,
    ):
        details = {}
        if action_name:
            details["action_name"] = action_name
        if instance_uuid:
            details["instance_uuid"] = instance_uuid
        if reason:
            details["reason"] = reason

        if detail:
            msg = detail
        elif action_name and reason:
            msg = f"{self.message} Название: {action_name}. Ошибка: {reason}"
        else:
            msg = self.message

        super().__init__(message=msg, details=details)


class SystemContractViolation(AutomationDomainException):
    """
    Invariant breach in runtime action dispatch.
    Stage-2 validation should prevent this path; if it happens, it is a server bug.
    """

    error_code = "SYSTEM_CONTRACT_VIOLATION"
    message = "Нарушен системный контракт исполнения триггера."

    def __init__(self, action_name: str, expected: str, got: str):
        details = {
            "action_name": action_name,
            "expected": expected,
            "got": got,
        }
        super().__init__(message=self.message, details=details)

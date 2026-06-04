# triggers/exceptions/service.py

from typing import Any, Dict, Optional
from exceptions.base import BaseAppException


class AutomationDomainException(BaseAppException):
    """Базовое исключение для всех сбоев в подсистеме триггеров и автоматизаций."""

    error_code = "AUTOMATION_DOMAIN_ERROR"


class AutomationActionNotFoundError(AutomationDomainException):
    """Выбрасывается, когда триггер ссылается на несуществующий экшен в реестре."""

    error_code = "AUTOMATION_ACTION_NOT_FOUND"
    message = "Запрошенный экшен автоматизации не зарегистрирован в системе."

    def __init__(self, action_name: str, trigger_name: str, instance_uuid: str):
        details = {
            "action_name": action_name,
            "trigger_name": trigger_name,
            "instance_uuid": instance_uuid,
        }
        super().__init__(
            message=f"{self.message} Экшен: {action_name}. Триггер: {trigger_name}",
            details=details,
        )


class AutomationConditionEvaluationError(AutomationDomainException):
    """Выбрасывается, когда парсинг AST-условия или расчет формулы завершился аварийно."""

    error_code = "AUTOMATION_CONDITION_EVALUATION_FAILED"
    message = "Ошибка при валидации или расчете AST-условия триггера."

    def __init__(
        self, trigger_name: str, reason: str, details: Optional[Dict[str, Any]] = None
    ):
        extended_details = {"trigger_name": trigger_name, "reason": reason}
        if details:
            extended_details["nested_exception"] = details
        super().__init__(
            message=f"{self.message} Триггер: {trigger_name}. Причина: {reason}",
            details=extended_details,
        )

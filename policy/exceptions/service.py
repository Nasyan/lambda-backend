# policy/exceptions/service.py

from typing import Optional
from uuid import UUID
from exceptions.base import BaseAppException


class PolicyDomainException(BaseAppException):
    """Базовое исключение для всех бизнес-ошибок подсистемы политик витрины."""

    error_code = "POLICY_DOMAIN_ERROR"


class PolicyTemplateNotFoundError(PolicyDomainException):
    """Выбрасывается, когда под витрину пытаются настроить несуществующую таблицу CRM."""

    error_code = "POLICY_TEMPLATE_NOT_FOUND"
    message = "Невозможно настроить витрину: указанная таблица отсутствует в системе."

    def __init__(
        self, instance_uuid: UUID, template_name: str, message: Optional[str] = None
    ):
        details = {"instance_uuid": str(instance_uuid), "template_name": template_name}
        super().__init__(message=message or self.message, details=details)


class PolicyAlreadyExistsError(PolicyDomainException):
    """Выбрасывается при попытке создать дублирующую политику безопасности для одного шаблона."""

    error_code = "POLICY_ALREADY_EXISTS"
    message = "Политика безопасности для данной таблицы уже настроена."

    def __init__(
        self, instance_uuid: UUID, template_name: str, message: Optional[str] = None
    ):
        details = {"instance_uuid": str(instance_uuid), "template_name": template_name}
        super().__init__(message=message or self.message, details=details)


class PolicyNotFoundError(PolicyDomainException):
    """Выбрасывается, когда запрашиваемая политика витрины (по ID) отсутствует в базе данных."""

    error_code = "POLICY_NOT_FOUND"
    message = "Указанная политика безопасности витрины не найдена."

    def __init__(
        self, instance_uuid: UUID, policy_id: UUID, message: Optional[str] = None
    ):
        details = {"instance_uuid": str(instance_uuid), "policy_id": str(policy_id)}
        super().__init__(message=message or self.message, details=details)

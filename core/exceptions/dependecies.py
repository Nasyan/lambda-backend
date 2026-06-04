# core/exceptions/dependecies.py

from typing import Any, Optional
from exceptions.base import BaseAppException


class InstanceDomainException(BaseAppException):
    """Базовое исключение для ошибок, связанных с инстансами и арендаторами (Multi-tenancy)."""

    error_code = "INSTANCE_DOMAIN_ERROR"


class UserInactiveError(InstanceDomainException):
    """Выбрасывается, если учетная запись пользователя заблокирована или неактивна."""

    error_code = "USER_ACCOUNT_INACTIVE"
    message = "Учетная запись пользователя неактивна."

    def __init__(self, user_uuid: Any):
        super().__init__(message=self.message, details={"user_uuid": str(user_uuid)})


class CreatorRoleRequiredError(InstanceDomainException):
    """Выбрасывается, когда действие требует роли CREATOR, но у пользователя другая роль."""

    error_code = "CREATOR_ROLE_REQUIRED"
    message = "Данное действие доступно только создателям инстансов (CREATOR)."

    def __init__(self, user_uuid: Any, current_role: str):
        super().__init__(
            message=self.message,
            details={"user_uuid": str(user_uuid), "current_role": current_role},
        )


class InstanceNotFoundError(InstanceDomainException):
    """Выбрасывается, когда запрашиваемый инстанс (организация/клиент) не найден в системе."""

    error_code = "INSTANCE_NOT_FOUND"
    message = "Запрашиваемый рабочий контур (инстанс) не найден."

    def __init__(self, instance_uuid: Any):
        super().__init__(
            message=self.message, details={"instance_uuid": str(instance_uuid)}
        )


class InstanceDeactivatedError(InstanceDomainException):
    """Выбрасывается, когда инстанс существует, но его обслуживание временно приостановлено."""

    error_code = "INSTANCE_DEACTIVATED"
    message = "Обслуживание данного рабочего контура (инстанса) приостановлено."

    def __init__(self, instance_uuid: Any):
        super().__init__(
            message=self.message, details={"instance_uuid": str(instance_uuid)}
        )


class InstanceAccessDeniedError(InstanceDomainException):
    """Выбрасывается при попытке получить доступ к чужому инстансу (Нарушение Multi-tenancy)."""

    error_code = "INSTANCE_ACCESS_DENIED"
    message = "У вас нет прав на управление этим инстансом."

    def __init__(
        self, user_uuid: Any, user_instance_id: Optional[Any], target_instance_uuid: Any
    ):
        super().__init__(
            message=self.message,
            details={
                "user_uuid": str(user_uuid),
                "user_instance_id": str(user_instance_id) if user_instance_id else None,
                "target_instance_uuid": str(target_instance_uuid),
            },
        )

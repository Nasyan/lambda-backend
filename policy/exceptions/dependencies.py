# policy/exceptions/dependencies.py

from policy.exceptions.service import PolicyDomainException
from typing import Optional
from uuid import UUID


class CrossTenantAccessDeniedError(PolicyDomainException):
    """Выбрасывается при попытке доступа или изменения данных чужого инстанса (пространства)."""

    error_code = "CROSS_TENANT_ACCESS_DENIED"
    message = (
        "У вас нет прав на управление конфигурациями витрины для этого пространства."
    )

    def __init__(
        self,
        user_uuid: UUID,
        user_instance_uuid: UUID,
        requested_instance_uuid: UUID,
        message: Optional[str] = None,
    ):
        details = {
            "user_uuid": str(user_uuid),
            "user_instance_uuid": str(user_instance_uuid),
            "requested_instance_uuid": str(requested_instance_uuid),
        }
        super().__init__(message=message or self.message, details=details)

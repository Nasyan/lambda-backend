# core/exceptions/permission.py

from typing import Any
from exceptions.base import BaseAppException


class SecurityDomainException(BaseAppException):
    """Базовое исключение для ошибок безопасности и контроля доступа."""

    error_code = "SECURITY_DOMAIN_ERROR"


class PermissionsNotConfiguredError(SecurityDomainException):
    """Выбрасывается, когда у пользователя вообще отсутствует объект разрешений."""

    error_code = "USER_PERMISSIONS_NOT_CONFIGURED"
    message = "Доступ к инструментам платформы не настроен."

    def __init__(self, user_uuid: Any, role: str):
        details = {"user_uuid": str(user_uuid), "role": str(role)}
        super().__init__(message=self.message, details=details)


class ToolAccessDeniedError(SecurityDomainException):
    """Выбрасывается, когда у пользователя нет прав на конкретный инструмент."""

    error_code = "TOOL_ACCESS_DENIED"

    def __init__(
        self, user_uuid: Any, role: str, required_tool: str, allowed_tools: list[str]
    ):
        message = f"У вас нет доступа к инструменту: {required_tool}"
        details = {
            "user_uuid": str(user_uuid),
            "role": str(role),
            "required_tool": required_tool,
            "allowed_tools": allowed_tools,
        }
        super().__init__(message=message, details=details)

from fastapi import status
from exceptions.base import BaseAppException


class ToolDisabledException(BaseAppException):
    """Исключение выбрасывается, если инструмент полностью отключен администратором."""

    error_code = "TOOL_DISABLED"
    status_code = status.HTTP_403_FORBIDDEN
    message = "Инструмент отключен администратором для данного инстанса."


class ToolActionForbiddenException(BaseAppException):
    """Исключение выбрасывается при нехватке конкретного права внутри инструмента."""

    error_code = "TOOL_ACTION_FORBIDDEN"
    status_code = status.HTTP_403_FORBIDDEN
    message = "У вас нет прав на выполнение данной операции внутри инструмента."

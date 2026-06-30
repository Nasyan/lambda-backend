from fastapi import status
from exceptions.base import BaseAppException


class InstanceConfigValidationError(BaseAppException):
    """Вызывается, если структура JSON-конфигурации в БД или при обновлении нарушена."""

    error_code = "CONFIG_VALIDATION_ERROR"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    message = "Ошибка валидации конфигурации инструментов инстанса."

# notifications/exceptions.py
from typing import Any, Dict, Optional
from exceptions.base import BaseAppException


class NotificationDomainException(BaseAppException):
    """Базовое исключение для всех сбоев в системе уведомлений платформы."""

    error_code = "NOTIFICATION_DOMAIN_ERROR"


class NotificationValidationError(NotificationDomainException):
    """Выбрасывается при передаче некорректных параметров (например, email канал без самого email-адреса)."""

    status_code = 400  # Явно указываем для обработчика ошибок
    error_code = "NOTIFICATION_VALIDATION_FAILED"
    message = "Неверные параметры для отправки уведомления."

    def __init__(
        self, message: Optional[str] = None, details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message=message or self.message, details=details)


class NotificationDispatchError(NotificationDomainException):
    """Выбрасывается, когда одна из систем доставки (БД или брокер очередей) дает сбой."""

    status_code = 500  # Критический сбой инфраструктуры доставки
    error_code = "NOTIFICATION_DISPATCH_FAILED"
    message = "Произошел критический сбой при отправке уведомления."

    def __init__(
        self,
        failed_channels: list[str],
        user_uuid: str,
        reason: str,
        message: Optional[str] = None,
    ):
        details = {
            "failed_channels": failed_channels,
            "user_uuid": user_uuid,
            "reason": reason,
        }
        super().__init__(message=message or self.message, details=details)


class NotificationNotFoundError(NotificationDomainException):
    """Выбрасывается, когда запрашиваемый шаблон или элемент инбокса не найден."""

    status_code = 404  # 🔥 Вот этот фикс! Перехватчик middleware теперь вернет 404
    error_code = "NOTIFICATION_NOT_FOUND"
    message = "Шаблон уведомления или запись в инбоксе не найдена."

    def __init__(
        self, message: Optional[str] = None, details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message=message or self.message, details=details)

# core/exceptions/history.py

from exceptions.base import BaseAppException


class UserInstanceNotFoundError(BaseAppException):
    """Вызывается, если у пользователя отсутствует привязка к инстансу компаний."""

    status_code = 500
    error_code = "USER_INSTANCE_NOT_FOUND"
    message = "У пользователя отсутствует привязка к инстансу компаний."

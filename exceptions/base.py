# exceptions/base.py

from typing import Any, Dict, Optional


class BaseAppException(Exception):
    """
    Базовый класс для ВСЕХ бизнес-ошибок платформы.
    """

    error_code: str = "INTERNAL_SERVER_ERROR"
    message: str = "Unexpected error"

    def __init__(
        self, message: Optional[str] = None, details: Optional[Dict[str, Any]] = None
    ):
        if message:
            self.message = message
        self.details = details or {}
        super().__init__(self.message)

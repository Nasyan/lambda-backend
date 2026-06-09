from typing import Any, Optional

from triggers.exceptions.action import AutomationDomainException


class RecordValidationError(AutomationDomainException):
    """Validation error for trigger schema/type contracts exposed to clients as 422."""

    status_code = 422
    error_code = "TRIGGER_RECORD_VALIDATION_ERROR"
    message = "Ошибка валидации триггера."

    def __init__(
        self,
        field: str,
        detail: str,
        expected: Optional[Any] = None,
        got: Optional[Any] = None,
    ) -> None:
        details = {
            "field": field,
            "expected": expected,
            "got": got,
            "detail": detail,
        }
        super().__init__(message=self.message, details=details)

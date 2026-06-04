# engine/exceptions.py

from engine.exceptions.evaluator import FormulaEngineException
from typing import Any, Optional


class FormulaTypeMismatchError(FormulaEngineException):
    """Выбрасывается, когда типы операндов не соответствуют математическому или строковому оператору."""

    error_code = "FORMULA_TYPE_MISMATCH"

    def __init__(
        self,
        operator_name: str,
        left_val: Any,
        right_val: Any,
        custom_message: Optional[str] = None,
    ):
        message = (
            custom_message
            or f"Несовместимые типы данных для оператора '{operator_name}'."
        )
        details = {
            "operator": operator_name,
            "left_type": type(left_val).__name__,
            "right_type": type(right_val).__name__,
            "left_preview": str(left_val)[:50],
            "right_preview": str(right_val)[:50],
        }
        super().__init__(message=message, details=details)


class FormulaValidationError(FormulaEngineException):
    error_code = "FORMULA_VALIDATION_ERROR"

    pass


class CircularDependencyError(FormulaEngineException):
    """Выбрасывается при обнаружении циклической зависимости в формулах (A -> B -> A)."""

    pass


class SchemaDependencyError(FormulaEngineException):
    """Выбрасывается, когда нельзя удалить/изменить поле или шаблон из-за активных связей."""

    pass


class SchemaValidationError(FormulaEngineException):
    pass

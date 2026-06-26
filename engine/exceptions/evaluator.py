# engine/exceptions/evaluator.py

from typing import Any, Optional
from exceptions.base import BaseAppException


class FormulaEngineException(BaseAppException):
    """Базовое исключение для всех ошибок движка формул и AST-обхода."""

    status_code = 422
    error_code = "FORMULA_ENGINE_ERROR"


class FormulaValidationError(BaseAppException):
    """Базовое исключение для всех ошибок движка формул и AST-обхода."""

    status_code = 422
    error_code = "FORMULA_INVALID_ERROR"


class FormulaEvaluationError(FormulaEngineException):
    """Общая ошибка при вычислении узла формулы."""

    error_code = "FORMULA_EVALUATION_ERROR"


class FormulaRelationTargetMissingError(FormulaEvaluationError):
    """Связь задана (relation_column не пуст), но резолвер не нашёл запись.

    Раньше этот случай молча возвращал 0 и портил суммы/средние по связям —
    это корень бага cost=0. Теперь падаем громко: вычисление колонки помечается
    как ошибочное (NULL + лог), а не подставляется правдоподобный, но неверный 0.
    """

    error_code = "FORMULA_RELATION_TARGET_MISSING"

    def __init__(self, relation_column: str, target_val: Any, target_field: str):
        message = (
            f"Связанная запись по '{relation_column}'={target_val} не найдена "
            f"(target_field='{target_field}'). Нельзя молча вернуть 0."
        )
        details = {
            "relation_column": relation_column,
            "target_value": str(target_val)[:80],
            "target_field": target_field,
        }
        super().__init__(message=message, details=details)


class FormulaDateFormatError(FormulaEngineException):
    """Выбрасывается, когда переданное значение невозможно распарсить как ISO дату."""

    error_code = "FORMULA_DATE_FORMAT_ERROR"
    message = "Ошибка валидации формата даты в формуле."

    def __init__(self, value: Any, reason: str):
        details = {
            "invalid_value": str(value),
            "value_type": type(value).__name__,
            "reason": reason,
        }
        super().__init__(message=f"{self.message} {reason}", details=details)


class FormulaResolverRequiredError(FormulaEngineException):
    """Выбрасывается, когда узел графа требует внешний резолвер данных, но он не был передан."""

    error_code = "FORMULA_RESOLVER_REQUIRED"

    def __init__(self, node_type: str, resolver_name: str):
        message = f"Для узла {node_type} требуется отсутствующий {resolver_name}."
        details = {"node_type": node_type, "missing_resolver": resolver_name}
        super().__init__(message=message, details=details)


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

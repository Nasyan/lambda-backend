# analytics/exceptions.py

from typing import Any, Dict, Optional
from exceptions.base import BaseAppException


class AnalyticsException(BaseAppException):
    """Базовый класс для всех ошибок модуля аналитики и BI."""

    error_code = "ANALYTICS_ERROR"
    message = "Ошибка в модуле аналитики."


class WidgetNotFoundError(AnalyticsException):
    """Выбрасывается, когда виджет не найден в базе данных или принадлежит другому инстансу."""

    error_code = "WIDGET_NOT_FOUND"
    message = "Виджет аналитики не найден или доступ к нему ограничен."

    def __init__(
        self,
        widget_uuid: Any,
        instance_uuid: Optional[Any] = None,
        message: Optional[str] = None,
    ):
        details = {"widget_uuid": str(widget_uuid)}
        if instance_uuid:
            details["instance_uuid"] = str(instance_uuid)

        super().__init__(message=message, details=details)


class AnalyticsCompilerException(BaseAppException):
    """Базовое исключение для ошибок компиляции AST в пайплайны СУБД."""

    error_code = "ANALYTICS_COMPILER_ERROR"
    message = "Ошибка при сборке аналитического запроса."


class UnsupportedASTNodeError(AnalyticsCompilerException):
    """Выбрасывается, если компилятор встретил неизвестный тип узла."""

    error_code = "UNSUPPORTED_AST_NODE"
    message = "Обнаружен неподдерживаемый тип узла формулы."

    def __init__(self, node_type: str, details: Optional[Dict[str, Any]] = None):
        merged_details = {"node_type": node_type}
        if details:
            merged_details.update(details)
        super().__init__(
            message=f"Тип узла '{node_type}' не поддерживается компилятором.",
            details=merged_details,
        )


class UnsupportedOperatorError(AnalyticsCompilerException):
    """Выбрасывается при попытке использовать несуществующий оператор (математический, строковый и т.д.)."""

    error_code = "UNSUPPORTED_OPERATOR"
    message = "Указан неподдерживаемый оператор вычисления."

    def __init__(self, operator_name: str, node_type: str):
        super().__init__(
            message=f"Оператор '{operator_name}' не поддерживается в узле типа '{node_type}'.",
            details={"operator": operator_name, "node_type": node_type},
        )


class InvalidAggregationConfigError(AnalyticsCompilerException):
    """Выбрасывается, если в графике настроена невалидная функция агрегации (оси X/Y)."""

    error_code = "INVALID_AGGREGATION_CONFIG"
    message = "Неверная конфигурация агрегации или группировки графика."

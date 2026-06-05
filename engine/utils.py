# engine/utils.py

import operator
from datetime import datetime
from typing import Any, Dict

from engine.exceptions.evaluator import FormulaDateFormatError

# Маппинг безопасных математических и логических операций
OPERATORS = {
    "add": operator.add,
    "subtract": operator.sub,
    "multiply": operator.mul,
    "divide": operator.truediv,
    "gt": operator.gt,
    "lt": operator.lt,
    "eq": operator.eq,
    "ne": operator.ne,
}


def parse_date(val: Any) -> datetime:
    """Универсальный парсер дат для вычислений."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            val = val.replace("Z", "+00:00")
            return datetime.fromisoformat(val)
        except ValueError:
            raise FormulaDateFormatError(
                value=val, reason="Expected ISO 8601 format (YYYY-MM-DDThh:mm:ss)."
            )
    raise FormulaDateFormatError(value=val, reason="Unsupported type context.")


def resolve_dot_notation(data: Dict[str, Any], path: str, default: Any = 0) -> Any:
    """
    Вспомогательный метод для безопасного извлечения вложенных данных по точечной нотации.
    """
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return default
    return current if current is not None else default

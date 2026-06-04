# engine/evaluator.py

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Callable, Awaitable

from engine.ast import (
    ASTNode,
    LiteralNode,
    FieldNode,
    InputNode,
    BinaryOpNode,
    RelationFieldNode,
    AggregationNode,
    ArrayReduceNode,
    ConditionNode,
    LogicalOpNode,
    DateOpNode,
    StringOpNode,
)

from engine.exceptions.evaluator import (
    FormulaResolverRequiredError,
    FormulaTypeMismatchError,
)
from logs.decorators import trace_action

# Импорты новых утилит и сервисов
from .utils import OPERATORS, parse_date, resolve_dot_notation
from .extractor import RelationExtractor


class FormulaEvaluator:
    """Асинхронный движок вычисления AST-дерева."""

    @classmethod
    @trace_action(name="AST_Engine::Evaluate")
    async def evaluate(
        cls,
        node: ASTNode,
        context: Dict[str, Any],
        record_resolver: Callable[[str], Awaitable[Dict[str, Any]]] = None,
        aggregation_resolver: Callable[..., Awaitable[Any]] = None,
    ) -> Any:
        if not node:
            return None

        # Оптимизация: "разглаживаем" контекст ровно один раз на старте
        if (
            isinstance(context, dict)
            and "data" in context
            and isinstance(context["data"], dict)
        ):
            context = {**context, **context["data"]}

        return await cls._eval_node(
            node, context, record_resolver, aggregation_resolver
        )

    @classmethod
    async def _eval_node(
        cls,
        node: ASTNode,
        context: Dict[str, Any],
        record_resolver: Callable,
        aggregation_resolver: Callable,
    ) -> Any:
        """Внутренний рекурсивный диспетчер."""
        if not node:
            return None

        handler_name = f"_eval_{node.type}"
        handler = getattr(cls, handler_name, None)

        if not handler:
            raise ValueError(f"Отсутствует обработчик для типа узла: {node.type}")

        return await handler(node, context, record_resolver, aggregation_resolver)

    # --- Обработчики конкретных узлов ---

    @classmethod
    async def _eval_literal(cls, node: LiteralNode, context, rr, ar):
        return node.value

    @classmethod
    async def _eval_field(cls, node: FieldNode, context, rr, ar):
        return resolve_dot_notation(context, node.value, default=0)

    @classmethod
    async def _eval_input(cls, node: InputNode, context, rr, ar):
        return context.get("__input_value__", "")

    @classmethod
    async def _eval_relation_field(cls, node: RelationFieldNode, context, rr, ar):
        target_val = context.get(node.relation_column)
        if not target_val:
            return 0
        if not rr:
            raise FormulaResolverRequiredError(
                node_type="RelationFieldNode", resolver_name="record_resolver"
            )

        lookup_field = node.lookup_field or "_id"
        target_record = await rr(target_val, lookup_field=lookup_field)

        if not target_record or "data" not in target_record:
            return 0

        return resolve_dot_notation(target_record["data"], node.target_field, default=0)

    @classmethod
    async def _eval_condition(cls, node: ConditionNode, context, rr, ar):
        cond_val = await cls._eval_node(node.condition, context, rr, ar)
        if cond_val:
            return await cls._eval_node(node.true_expr, context, rr, ar)
        return await cls._eval_node(node.false_expr, context, rr, ar)

    @classmethod
    async def _eval_logical_op(cls, node: LogicalOpNode, context, rr, ar):
        left_val = await cls._eval_node(node.left, context, rr, ar)

        if node.operator == "not":
            return not bool(left_val)

        if node.operator == "and" and not left_val:
            return False
        if node.operator == "or" and left_val:
            return True

        right_val = await cls._eval_node(node.right, context, rr, ar)

        if node.operator == "and":
            return bool(left_val and right_val)
        if node.operator == "or":
            return bool(left_val or right_val)

    @classmethod
    async def _eval_date_op(cls, node: DateOpNode, context, rr, ar):
        if node.operator == "now":
            return datetime.now(timezone.utc).isoformat()

        left_val = await cls._eval_node(node.left, context, rr, ar)
        right_val = (
            await cls._eval_node(node.right, context, rr, ar) if node.right else 0
        )

        dt = parse_date(left_val)

        try:
            if node.operator == "add_days":
                return (dt + timedelta(days=float(right_val))).isoformat()
            elif node.operator == "sub_days":
                return (dt - timedelta(days=float(right_val))).isoformat()
            elif node.operator == "diff_days":
                dt_right = parse_date(right_val)
                return (dt - dt_right).days
        except (ValueError, TypeError):
            raise FormulaTypeMismatchError(
                operator_name=node.operator, left_val=left_val, right_val=right_val
            )

    @classmethod
    async def _eval_string_op(cls, node: StringOpNode, context, rr, ar):
        left_val = str(await cls._eval_node(node.left, context, rr, ar) or "")

        if node.operator == "lower":
            return left_val.lower()
        if node.operator == "upper":
            return left_val.upper()

        right_val = str(await cls._eval_node(node.right, context, rr, ar) or "")

        if node.operator == "concat":
            return left_val + right_val
        elif node.operator == "regex_match":
            return bool(re.search(right_val, left_val))
        elif node.operator == "regex_extract":
            match = re.search(right_val, left_val)
            return match.group(1) if match and match.groups() else ""

    @classmethod
    async def _eval_aggregation(cls, node: AggregationNode, context, rr, ar):
        if not ar:
            raise FormulaResolverRequiredError(
                node_type="AggregationNode", resolver_name="aggregation_resolver"
            )
        resolved_filter_value = await cls._eval_node(node.filter_value, context, rr, ar)
        return await ar(
            node.target_template_uuid,
            node.filter_field,
            resolved_filter_value,
            node.agg_function,
            node.agg_field,
        )

    @classmethod
    async def _eval_array_reduce(cls, node: ArrayReduceNode, context, rr, ar):
        items = context.get(node.array_field)
        if not items or not isinstance(items, list):
            return 0 if node.agg_function in ("sum", "count", "avg") else None

        # Используем выделенный сервис для извлечения связей
        required_relation_cols = set()
        if node.item_expression:
            RelationExtractor.traverse(node.item_expression, required_relation_cols)
        if node.filter_expression:
            RelationExtractor.traverse(node.filter_expression, required_relation_cols)

        if required_relation_cols and hasattr(rr, "prefetch"):
            ids_to_fetch = set()
            for item in items:
                if isinstance(item, dict):
                    for col in required_relation_cols:
                        val = item.get(col)
                        if val:
                            ids_to_fetch.add(val)
            if ids_to_fetch:
                await rr.prefetch(list(ids_to_fetch))

        evaluated_values = []
        for item in items:
            item_context = item if isinstance(item, dict) else {"value": item}

            if node.filter_expression:
                is_match = await cls._eval_node(
                    node.filter_expression, item_context, rr, ar
                )
                if not is_match:
                    continue

            if node.agg_function == "count":
                evaluated_values.append(1)
                continue

            val = await cls._eval_node(node.item_expression, item_context, rr, ar)
            if val is not None and isinstance(val, (int, float)):
                evaluated_values.append(float(val))

        if not evaluated_values:
            return 0 if node.agg_function in ("sum", "count", "avg") else None

        if node.agg_function == "sum":
            return sum(evaluated_values)
        elif node.agg_function == "count":
            return len(evaluated_values)
        elif node.agg_function == "avg":
            return sum(evaluated_values) / len(evaluated_values)
        elif node.agg_function == "min":
            return min(evaluated_values)
        elif node.agg_function == "max":
            return max(evaluated_values)

    @classmethod
    async def _eval_binary_op(cls, node: BinaryOpNode, context, rr, ar):
        left_val = await cls._eval_node(node.left, context, rr, ar)
        right_val = await cls._eval_node(node.right, context, rr, ar)

        if left_val is None or right_val is None:
            return None

        if node.operator in ("subtract", "multiply", "divide"):
            if not isinstance(left_val, (int, float)) or not isinstance(
                right_val, (int, float)
            ):
                raise FormulaTypeMismatchError(
                    operator_name=node.operator,
                    left_val=left_val,
                    right_val=right_val,
                    custom_message=f"Оператор '{node.operator}' требует численных операндов.",
                )

        elif node.operator == "add":
            if not (
                isinstance(left_val, (int, float))
                and isinstance(right_val, (int, float))
            ) and not (isinstance(left_val, str) and isinstance(right_val, str)):
                raise FormulaTypeMismatchError(
                    operator_name="add",
                    left_val=left_val,
                    right_val=right_val,
                    custom_message="Несоответствие типов при сложении (ожидались Число+Число или Строка+Строка).",
                )

        op_func = OPERATORS.get(node.operator)
        try:
            return op_func(left_val, right_val)
        except ZeroDivisionError:
            return None

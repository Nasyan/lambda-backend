# engine/evaluator.py

import re
import structlog
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

from .utils import OPERATORS, parse_date, resolve_dot_notation
from .extractor import RelationExtractor

# Инициализируем structlog
logger = structlog.get_logger(__name__)


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
            logger.debug("evaluate_empty_node")
            return None

        # Передаем ключи контекста как отдельное поле для JSON
        logger.info(
            "formula_evaluation_started",
            context_keys=list(context.keys()) if context else None,
        )

        if (
            isinstance(context, dict)
            and "data" in context
            and isinstance(context["data"], dict)
        ):
            context = {**context, **context["data"]}

        try:
            result = await cls._eval_node(
                node, context, record_resolver, aggregation_resolver
            )
            logger.info("formula_evaluation_success", result=result)
            return result
        except Exception as e:
            # exc_info=True автоматически подтянется вашим structlog.processors.format_exc_info
            logger.error("formula_evaluation_failed", error=str(e), exc_info=True)
            raise

    @classmethod
    async def _eval_node(
        cls,
        node: ASTNode,
        context: Dict[str, Any],
        record_resolver: Callable,
        aggregation_resolver: Callable,
    ) -> Any:
        if not node:
            return None

        handler_name = f"_eval_{node.type}"
        handler = getattr(cls, handler_name, None)

        # Биндим тип узла к логгеру, чтобы не передавать его каждый раз
        node_logger = logger.bind(node_type=node.type)

        if not handler:
            node_logger.error("missing_handler")
            raise ValueError(f"Отсутствует обработчик для типа узла: {node.type}")

        node_logger.debug("evaluating_node")

        result = await handler(node, context, record_resolver, aggregation_resolver)

        node_logger.debug("node_evaluated", result=result)
        return result

    # --- Обработчики конкретных узлов ---

    @classmethod
    async def _eval_literal(cls, node: LiteralNode, context, rr, ar):
        return node.value

    @classmethod
    async def _eval_field(cls, node: FieldNode, context, rr, ar):
        val = resolve_dot_notation(context, node.value, default=0)
        logger.debug("field_resolved", path=node.value, resolved_value=val)
        return val

    @classmethod
    async def _eval_input(cls, node: InputNode, context, rr, ar):
        return context.get("__input_value__", "")

    @classmethod
    async def _eval_relation_field(cls, node: RelationFieldNode, context, rr, ar):
        target_val = context.get(node.relation_column)
        if not target_val:
            logger.debug("relation_column_empty", column=node.relation_column)
            return 0

        if not rr:
            logger.error("missing_record_resolver", node_type="RelationFieldNode")
            raise FormulaResolverRequiredError(
                node_type="RelationFieldNode", resolver_name="record_resolver"
            )

        lookup_field = node.lookup_field or "_id"
        logger.debug(
            "fetching_related_record",
            column=node.relation_column,
            target_val=target_val,
            lookup_field=lookup_field,
        )

        target_record = await rr(target_val, lookup_field=lookup_field)

        if not target_record or "data" not in target_record:
            logger.warning("target_record_not_found", target_val=target_val)
            return 0

        val = resolve_dot_notation(target_record["data"], node.target_field, default=0)
        logger.debug(
            "relation_field_resolved", target_field=node.target_field, value=val
        )
        return val

    @classmethod
    async def _eval_condition(cls, node: ConditionNode, context, rr, ar):
        cond_val = await cls._eval_node(node.condition, context, rr, ar)
        logger.debug("condition_evaluated", condition_result=cond_val)

        if cond_val:
            return await cls._eval_node(node.true_expr, context, rr, ar)
        return await cls._eval_node(node.false_expr, context, rr, ar)

    @classmethod
    async def _eval_logical_op(cls, node: LogicalOpNode, context, rr, ar):
        left_val = await cls._eval_node(node.left, context, rr, ar)

        if node.operator == "not":
            return not bool(left_val)

        if node.operator == "and" and not left_val:
            logger.debug(
                "logical_op_short_circuit", operator="and", reason="left_is_false"
            )
            return False
        if node.operator == "or" and left_val:
            logger.debug(
                "logical_op_short_circuit", operator="or", reason="left_is_true"
            )
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
        except (ValueError, TypeError) as e:
            logger.error(
                "date_op_type_mismatch",
                operator=node.operator,
                left_val=left_val,
                right_val=right_val,
                error=str(e),
            )
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
            logger.error("missing_aggregation_resolver", node_type="AggregationNode")
            raise FormulaResolverRequiredError(
                node_type="AggregationNode", resolver_name="aggregation_resolver"
            )

        resolved_filter_value = await cls._eval_node(node.filter_value, context, rr, ar)
        logger.debug("aggregation_filter_resolved", filter_value=resolved_filter_value)

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
        logger.debug(
            "array_reduce_started",
            array_field=node.array_field,
            items_count=len(items) if isinstance(items, list) else 0,
        )

        if not items or not isinstance(items, list):
            return 0 if node.agg_function in ("sum", "count", "avg") else None

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
                logger.debug("prefetching_relations", ids=list(ids_to_fetch))
                await rr.prefetch(list(ids_to_fetch))

        evaluated_values = []
        for i, item in enumerate(items):
            if isinstance(item, dict):
                item_context = {**context, **item}
                if node.array_field not in item and "target_uuid" in item:
                    item_context[node.array_field] = item["target_uuid"]
            else:
                item_context = {**context, "value": item}

            if node.filter_expression:
                is_match = await cls._eval_node(
                    node.filter_expression, item_context, rr, ar
                )
                if not is_match:
                    logger.debug("array_reduce_item_filtered", index=i)
                    continue

            if node.agg_function == "count":
                evaluated_values.append(1)
                continue

            val = await cls._eval_node(node.item_expression, item_context, rr, ar)
            if val is not None and isinstance(val, (int, float)):
                evaluated_values.append(float(val))
            else:
                logger.debug("array_reduce_item_not_numeric", index=i, value=val)

        logger.debug("array_reduce_collected", values=evaluated_values)

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
            logger.debug(
                "binary_op_none_operand",
                operator=node.operator,
                left=left_val,
                right=right_val,
            )
            return None

        if node.operator in ("subtract", "multiply", "divide"):
            if not isinstance(left_val, (int, float)) or not isinstance(
                right_val, (int, float)
            ):
                logger.error(
                    "binary_op_type_mismatch",
                    operator=node.operator,
                    left_type=str(type(left_val)),
                    right_type=str(type(right_val)),
                )
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
                logger.error(
                    "add_op_type_mismatch",
                    left_type=str(type(left_val)),
                    right_type=str(type(right_val)),
                )
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
            logger.warning("division_by_zero", left=left_val, right=right_val)
            return None

# engine/evaluator.py

import re
import structlog
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

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
    ObjectNode,
    QueryNode,
    StringOpNode,
)
from engine.batch_loader import BatchDataLoader

from engine.exceptions.evaluator import (
    FormulaResolverRequiredError,
    FormulaTypeMismatchError,
)
from logs.decorators import trace_action

from .utils import OPERATORS, parse_date, resolve_dot_notation
from .extractor import RelationExtractor

# Инициализируем structlog
logger = structlog.get_logger(__name__)


@dataclass
class EvaluationScope:
    document: Dict[str, Any]
    instance_uuid: str
    current_item: Optional[Any] = None
    variables: Dict[str, Any] = field(default_factory=dict)
    source_schema: Dict[str, Any] = field(default_factory=dict)
    # Снимок документа ДО обновления (события UPDATE). Доступен в AST через
    # $old.<field>; текущее состояние — через $new.<field> (alias документа).
    # Нужен для идемпотентности: condition_ast может проверять факт ИЗМЕНЕНИЯ
    # поля, а не просто его значение (task3, ГЗ-2 п.1).
    previous_document: Optional[Dict[str, Any]] = None

    def child_for_item(self, item: Any) -> "EvaluationScope":
        child_variables = {**self.variables, "current_item": item}
        return EvaluationScope(
            document=self.document,
            instance_uuid=self.instance_uuid,
            current_item=item,
            variables=child_variables,
            source_schema=self.source_schema,
            previous_document=self.previous_document,
        )

    @staticmethod
    def _document_context(document: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Плоский контекст одного документа: верхний уровень + data."""
        context: Dict[str, Any] = {}
        if isinstance(document, dict):
            context.update(document)
            data = document.get("data")
            if isinstance(data, dict):
                context.update(data)
        return context

    def as_context(self) -> Dict[str, Any]:
        context: Dict[str, Any] = {}
        if isinstance(self.document, dict):
            context.update(self.document)
            data = self.document.get("data")
            if isinstance(data, dict):
                context.update(data)
        if isinstance(self.current_item, dict):
            context.update(self.current_item)
            item_data = self.current_item.get("data")
            if isinstance(item_data, dict):
                context.update(item_data)
        elif self.current_item is not None:
            context["value"] = self.current_item
        context.update(self.variables)
        return context


class ASTEvaluator:
    """Stateful AST evaluator used by trigger-engine v2 automation pipelines."""

    def __init__(
        self,
        batch_loader: Optional[BatchDataLoader] = None,
        record_resolver: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
        aggregation_resolver: Optional[Callable[..., Awaitable[Any]]] = None,
    ):
        self.batch_loader = batch_loader
        self.record_resolver = record_resolver
        self.aggregation_resolver = aggregation_resolver

    @trace_action(name="AST_Engine_V2::Evaluate")
    async def evaluate(self, node: ASTNode, scope: EvaluationScope) -> Any:
        if not node:
            logger.debug("evaluate_empty_node")
            return None

        handler_name = f"_eval_{node.type}"
        handler = getattr(self, handler_name, None)
        if not handler:
            logger.error("missing_handler", node_type=node.type)
            raise ValueError(f"Отсутствует обработчик для типа узла: {node.type}")

        return await handler(node, scope)

    async def _eval_literal(self, node: LiteralNode, scope: EvaluationScope) -> Any:
        return node.value

    async def _eval_field(self, node: FieldNode, scope: EvaluationScope) -> Any:
        if node.value == "current_item":
            return scope.current_item
        if node.value.startswith("current_item."):
            return resolve_dot_notation(
                {"current_item": scope.current_item},
                node.value,
                default=0,
            )

        # $old/$new — трекинг состояний для событий UPDATE (идемпотентность):
        # $old.<field> читает снимок документа ДО изменения, $new.<field> —
        # текущее состояние. На событиях без previous_document $old.* == None,
        # поэтому условия вида ne($new.x, $old.x) корректно срабатывают и на CREATE.
        if node.value == "$old":
            return EvaluationScope._document_context(scope.previous_document)
        if node.value.startswith("$old."):
            old_context = EvaluationScope._document_context(scope.previous_document)
            value = resolve_dot_notation(old_context, node.value[5:], default=None)
            logger.debug("old_field_resolved", path=node.value, resolved_value=value)
            return value
        if node.value == "$new":
            return EvaluationScope._document_context(scope.document)
        if node.value.startswith("$new."):
            new_context = EvaluationScope._document_context(scope.document)
            value = resolve_dot_notation(new_context, node.value[5:], default=None)
            logger.debug("new_field_resolved", path=node.value, resolved_value=value)
            return value

        context = scope.as_context()
        value = resolve_dot_notation(context, node.value, default=0)
        logger.debug("field_resolved", path=node.value, resolved_value=value)
        return value

    async def _eval_input(self, node: InputNode, scope: EvaluationScope) -> Any:
        if "__input_value__" in scope.variables:
            return scope.variables["__input_value__"]
        if "input" in scope.variables:
            return scope.variables["input"]
        context = scope.as_context()
        if "__input_value__" in context:
            return context["__input_value__"]
        return context.get("input", "")

    async def _eval_relation_field(
        self, node: RelationFieldNode, scope: EvaluationScope
    ) -> Any:
        context = scope.as_context()
        relation_value = resolve_dot_notation(context, node.relation_column, default=0)
        if not relation_value:
            logger.debug("relation_column_empty", column=node.relation_column)
            return 0

        relation_items = (
            relation_value if isinstance(relation_value, list) else [relation_value]
        )
        relation_ids = [
            self._extract_relation_id(item)
            for item in relation_items
            if self._extract_relation_id(item)
        ]
        if not relation_ids:
            return [] if isinstance(relation_value, list) else 0

        target_template_uuid = self._relation_target_template(
            scope.source_schema, node.relation_column
        )
        records = await self._resolve_related_records(
            template_uuid=target_template_uuid,
            record_ids=relation_ids,
            lookup_field=node.lookup_field or "_id",
        )

        resolved_values = [
            resolve_dot_notation(record.get("data", {}), node.target_field, default=0)
            for record in records
            if record
        ]
        if isinstance(relation_value, list):
            return resolved_values
        return resolved_values[0] if resolved_values else 0

    async def _eval_condition(self, node: ConditionNode, scope: EvaluationScope) -> Any:
        condition_value = await self.evaluate(node.condition, scope)
        if condition_value:
            return await self.evaluate(node.true_expr, scope)
        return await self.evaluate(node.false_expr, scope)

    async def _eval_logical_op(
        self, node: LogicalOpNode, scope: EvaluationScope
    ) -> bool:
        left_value = await self.evaluate(node.left, scope)
        if node.operator == "not":
            return not bool(left_value)
        if node.operator == "and" and not left_value:
            return False
        if node.operator == "or" and left_value:
            return True

        right_value = await self.evaluate(node.right, scope)
        if node.operator == "and":
            return bool(left_value and right_value)
        if node.operator == "or":
            return bool(left_value or right_value)
        return False

    async def _eval_date_op(self, node: DateOpNode, scope: EvaluationScope) -> Any:
        if node.operator == "now":
            return datetime.now(timezone.utc).isoformat()

        left_value = await self.evaluate(node.left, scope)
        right_value = await self.evaluate(node.right, scope) if node.right else 0
        dt = parse_date(left_value)

        try:
            if node.operator == "add_days":
                return (dt + timedelta(days=float(right_value))).isoformat()
            if node.operator == "sub_days":
                return (dt - timedelta(days=float(right_value))).isoformat()
            if node.operator == "diff_days":
                dt_right = parse_date(right_value)
                return (dt - dt_right).days
        except (ValueError, TypeError) as exc:
            logger.error(
                "date_op_type_mismatch",
                operator=node.operator,
                left_val=left_value,
                right_val=right_value,
                error=str(exc),
            )
            raise FormulaTypeMismatchError(
                operator_name=node.operator,
                left_val=left_value,
                right_val=right_value,
            ) from exc
        return None

    async def _eval_string_op(self, node: StringOpNode, scope: EvaluationScope) -> Any:
        left_value = str(await self.evaluate(node.left, scope) or "")
        if node.operator == "lower":
            return left_value.lower()
        if node.operator == "upper":
            return left_value.upper()

        right_value = str(await self.evaluate(node.right, scope) or "")
        if node.operator == "concat":
            return left_value + right_value
        if node.operator == "regex_match":
            return bool(re.search(right_value, left_value))
        if node.operator == "regex_extract":
            match = re.search(right_value, left_value)
            return match.group(1) if match and match.groups() else ""
        return ""

    async def _eval_aggregation(
        self, node: AggregationNode, scope: EvaluationScope
    ) -> Any:
        filter_value = await self.evaluate(node.filter_value, scope)
        if self.batch_loader:
            return await self.batch_loader.aggregate_records(
                target_template_uuid=node.target_template_uuid,
                filter_field=node.filter_field,
                filter_value=filter_value,
                agg_function=node.agg_function,
                agg_field=node.agg_field,
            )
        if self.aggregation_resolver:
            return await self.aggregation_resolver(
                node.target_template_uuid,
                node.filter_field,
                filter_value,
                node.agg_function,
                node.agg_field,
            )
        raise FormulaResolverRequiredError(
            node_type="AggregationNode",
            resolver_name="aggregation_resolver",
        )

    async def _eval_array_reduce(
        self, node: ArrayReduceNode, scope: EvaluationScope
    ) -> Any:
        context = scope.as_context()
        items = resolve_dot_notation(context, node.array_field, default=[])
        if not items or not isinstance(items, list):
            return 0 if node.agg_function in ("sum", "count", "avg") else None

        await self._prefetch_array_relations(items, node)
        evaluated_values = []
        for item in items:
            item_scope = scope.child_for_item(item)
            if node.filter_expression:
                is_match = await self.evaluate(node.filter_expression, item_scope)
                if not is_match:
                    continue

            if node.agg_function == "count":
                evaluated_values.append(1)
                continue

            value = await self.evaluate(node.item_expression, item_scope)
            if value is not None and isinstance(value, (int, float)):
                evaluated_values.append(float(value))

        if not evaluated_values:
            return 0 if node.agg_function in ("sum", "count", "avg") else None
        if node.agg_function == "sum":
            return sum(evaluated_values)
        if node.agg_function == "count":
            return len(evaluated_values)
        if node.agg_function == "avg":
            return sum(evaluated_values) / len(evaluated_values)
        if node.agg_function == "min":
            return min(evaluated_values)
        if node.agg_function == "max":
            return max(evaluated_values)
        return None

    async def _eval_object(
        self, node: ObjectNode, scope: EvaluationScope
    ) -> Dict[str, Any]:
        return {
            field_name: await self.evaluate(field_node, scope)
            for field_name, field_node in node.fields.items()
        }

    async def _eval_query(
        self, node: QueryNode, scope: EvaluationScope
    ) -> List[Dict[str, Any]]:
        if not self.batch_loader:
            raise FormulaResolverRequiredError(
                node_type="QueryNode",
                resolver_name="batch_loader",
            )

        filters = []
        for item in node.filters:
            filters.append(
                {
                    "field": item.field,
                    "operator": item.operator,
                    "value": await self.evaluate(item.value, scope),
                }
            )

        return await self.batch_loader.query_records(
            target_template_uuid=node.target_template_uuid,
            filters=filters,
            limit=node.limit,
            return_fields=node.return_fields,
        )

    async def _eval_binary_op(self, node: BinaryOpNode, scope: EvaluationScope) -> Any:
        left_value = await self.evaluate(node.left, scope)
        right_value = await self.evaluate(node.right, scope)

        # eq/ne обязаны работать с None-операндами: $old.<field> на событии
        # CREATE (или для ранее отсутствовавшего поля) равен None, и условие
        # "поле изменилось" должно вычисляться, а не схлопываться в None.
        if node.operator in ("eq", "ne"):
            op_func = OPERATORS[node.operator]
            return op_func(left_value, right_value)

        if left_value is None or right_value is None:
            return None

        if node.operator in ("subtract", "multiply", "divide"):
            if not isinstance(left_value, (int, float)) or not isinstance(
                right_value, (int, float)
            ):
                raise FormulaTypeMismatchError(
                    operator_name=node.operator,
                    left_val=left_value,
                    right_val=right_value,
                    custom_message=(
                        f"Оператор '{node.operator}' " "требует численных операндов."
                    ),
                )
        elif node.operator == "add":
            numbers = isinstance(left_value, (int, float)) and isinstance(
                right_value, (int, float)
            )
            strings = isinstance(left_value, str) and isinstance(right_value, str)
            if not numbers and not strings:
                raise FormulaTypeMismatchError(
                    operator_name="add",
                    left_val=left_value,
                    right_val=right_value,
                    custom_message=(
                        "Несоответствие типов при сложении "
                        "(ожидались Число+Число или Строка+Строка)."
                    ),
                )

        op_func = OPERATORS.get(node.operator)
        try:
            return op_func(left_value, right_value)
        except ZeroDivisionError:
            logger.warning("division_by_zero", left=left_value, right=right_value)
            return None

    async def _resolve_related_records(
        self,
        template_uuid: Optional[str],
        record_ids: List[str],
        lookup_field: str,
    ) -> List[Dict[str, Any]]:
        if self.batch_loader:
            if lookup_field == "_id":
                records_map = await self.batch_loader.get_many(
                    template_uuid, record_ids
                )
                return [
                    records_map[record_id]
                    for record_id in record_ids
                    if record_id in records_map
                ]
            records_map = await self.batch_loader.get_by_field_many(
                template_uuid, lookup_field, record_ids
            )
            return [
                records_map[record_id]
                for record_id in record_ids
                if record_id in records_map
            ]

        if self.record_resolver:
            records = []
            for record_id in record_ids:
                record = await self.record_resolver(
                    record_id, lookup_field=lookup_field
                )
                if record:
                    records.append(record)
            return records

        raise FormulaResolverRequiredError(
            node_type="RelationFieldNode",
            resolver_name="record_resolver",
        )

    async def _prefetch_array_relations(
        self, items: List[Any], node: ArrayReduceNode
    ) -> None:
        if not self.batch_loader:
            return

        required_relation_cols = set()
        if node.item_expression:
            RelationExtractor.traverse(node.item_expression, required_relation_cols)
        if node.filter_expression:
            RelationExtractor.traverse(node.filter_expression, required_relation_cols)

        relation_ids = []
        for item in items:
            if not isinstance(item, dict):
                continue
            for relation_col in required_relation_cols:
                relation_id = self._extract_relation_id(item.get(relation_col))
                if relation_id:
                    relation_ids.append(relation_id)
        if relation_ids:
            await self.batch_loader.prefetch(None, relation_ids)

    def _relation_target_template(
        self, source_schema: Dict[str, Any], relation_column: str
    ) -> Optional[str]:
        field_meta = source_schema.get(relation_column.split(".", 1)[0], {})
        target_template_uuid = field_meta.get("target_template_uuid")
        return str(target_template_uuid) if target_template_uuid else None

    def _extract_relation_id(self, relation_item: Any) -> Optional[str]:
        if not relation_item:
            return None
        if isinstance(relation_item, dict):
            raw_id = (
                relation_item.get("target_uuid")
                or relation_item.get("_id")
                or relation_item.get("uuid")
            )
            return str(raw_id) if raw_id else None
        return str(relation_item)


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

        if node.operator in ("eq", "ne"):
            return OPERATORS[node.operator](left_val, right_val)

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

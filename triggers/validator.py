from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from engine.ast import (
    AggregationNode,
    ArrayReduceNode,
    BinaryOpNode,
    ConditionNode,
    DateOpNode,
    FieldNode,
    InputNode,
    LiteralNode,
    LogicalOpNode,
    ObjectNode,
    QueryNode,
    RelationFieldNode,
    StringOpNode,
    parse_ast,
)
from engine.exceptions.evaluator import FormulaValidationError
from mongo.exceptions.template import TemplateNotFoundError
from mongo.template import TemplateRepository
from triggers.action_contracts import DML_ACTION_NAMES, get_action_signature
from triggers.exceptions.validation import RecordValidationError
from triggers.models import PayloadReturnType, Trigger


class TriggerSchemaValidator:
    """
    Validates persisted trigger metadata before create/update.

    LIST inference rules:
    - FieldNode returns LIST only when the source schema field is a collection:
      relation_list, array/list-like type, or explicit multiple/is_array metadata.
    - RelationFieldNode returns LIST when its relation_column has multi cardinality.
      Scalar relation columns return VALUE.
    - QueryNode is a standalone LIST-producing node for data lookups.
    - Aggregation and array_reduce collapse collections to VALUE.
    """

    LIST_FIELD_TYPES = {
        "array",
        "list",
        "relation_list",
        "relation_multi",
        "multi_relation",
    }

    COMPARISON_OPERATORS = {"gt", "lt", "eq", "ne"}
    ARITHMETIC_OPERATORS = {"add", "subtract", "multiply", "divide"}

    async def validate(
        self,
        trigger_data: Mapping[str, Any],
        db: AsyncSession,
        template_repo: TemplateRepository,
        trigger_uuid: Optional[UUID] = None,
    ) -> PayloadReturnType:
        instance_uuid = self._require_value(trigger_data, "instance_uuid")
        source_template_uuid = self._require_value(
            trigger_data, "source_template_uuid"
        )
        target_template_uuid = self._require_value(
            trigger_data, "target_template_uuid"
        )

        source_template = await self._get_template(
            template_repo=template_repo,
            instance_uuid=instance_uuid,
            template_uuid=source_template_uuid,
            field="source_template_uuid",
        )
        await self._get_template(
            template_repo=template_repo,
            instance_uuid=instance_uuid,
            template_uuid=target_template_uuid,
            field="target_template_uuid",
        )

        source_schema = source_template.get("schema", {})
        payload_node = self._parse_tree(
            trigger_data.get("payload_ast"), field="payload_ast"
        )
        payload_return_type = self.infer_return_type(
            payload_node,
            source_schema=source_schema,
            field="payload_ast",
        )

        condition_ast = trigger_data.get("condition_ast")
        if condition_ast:
            condition_type = self.infer_return_type(
                self._parse_tree(condition_ast, field="condition_ast"),
                source_schema=source_schema,
                field="condition_ast",
            )
            self._ensure_type(
                field="condition_ast",
                expected=PayloadReturnType.BOOLEAN,
                got=condition_type,
                detail="condition_ast must evaluate to BOOLEAN.",
            )

        self._validate_action_contract(
            action_name=trigger_data.get("action_name"),
            payload_return_type=payload_return_type,
            action_mapping_ast=trigger_data.get("action_mapping_ast"),
        )

        action_mapping_ast = trigger_data.get("action_mapping_ast")
        if action_mapping_ast:
            self._parse_tree(action_mapping_ast, field="action_mapping_ast")

        if self._is_dml_action(trigger_data.get("action_name")):
            self._validate_dml_target_consistency(
                target_template_uuid=target_template_uuid,
                action_params=trigger_data.get("action_params"),
            )
            await self._validate_no_cycles(
                db=db,
                instance_uuid=instance_uuid,
                source_template_uuid=source_template_uuid,
                target_template_uuid=target_template_uuid,
                trigger_uuid=trigger_uuid,
            )

        return payload_return_type

    def infer_return_type(
        self,
        node: Any,
        source_schema: Dict[str, Any],
        field: str,
    ) -> PayloadReturnType:
        if isinstance(node, LiteralNode):
            if isinstance(node.value, bool):
                return PayloadReturnType.BOOLEAN
            return PayloadReturnType.VALUE

        if isinstance(node, InputNode):
            return PayloadReturnType.VALUE

        if isinstance(node, FieldNode):
            return self._infer_field_node(node, source_schema, field)

        if isinstance(node, RelationFieldNode):
            return self._infer_relation_field_node(node, source_schema, field)

        if isinstance(node, AggregationNode):
            self.infer_return_type(
                node.filter_value,
                source_schema=source_schema,
                field=f"{field}.filter_value",
            )
            return PayloadReturnType.VALUE

        if isinstance(node, ArrayReduceNode):
            array_type = self._return_type_for_field(
                field_name=node.array_field,
                source_schema=source_schema,
                field=f"{field}.array_field",
            )
            self._ensure_type(
                field=f"{field}.array_field",
                expected=PayloadReturnType.LIST,
                got=array_type,
                detail="array_reduce requires a LIST source field.",
            )
            return PayloadReturnType.VALUE

        if isinstance(node, ObjectNode):
            for field_name, child_node in node.fields.items():
                self.infer_return_type(
                    child_node,
                    source_schema=source_schema,
                    field=f"{field}.fields.{field_name}",
                )
            return PayloadReturnType.VALUE

        if isinstance(node, QueryNode):
            for idx, query_filter in enumerate(node.filters):
                self.infer_return_type(
                    query_filter.value,
                    source_schema=source_schema,
                    field=f"{field}.filters.{idx}.value",
                )
            return PayloadReturnType.LIST

        if isinstance(node, BinaryOpNode):
            left_type = self.infer_return_type(
                node.left, source_schema=source_schema, field=f"{field}.left"
            )
            right_type = self.infer_return_type(
                node.right, source_schema=source_schema, field=f"{field}.right"
            )
            if node.operator in self.COMPARISON_OPERATORS:
                self._ensure_scalar_operand(
                    operand_type=left_type,
                    field=f"{field}.left",
                    operator=node.operator,
                )
                self._ensure_scalar_operand(
                    operand_type=right_type,
                    field=f"{field}.right",
                    operator=node.operator,
                )
                return PayloadReturnType.BOOLEAN
            if node.operator in self.ARITHMETIC_OPERATORS:
                if PayloadReturnType.LIST in {left_type, right_type}:
                    raise RecordValidationError(
                        field=field,
                        expected="VALUE operands",
                        got=f"{left_type.value}, {right_type.value}",
                        detail=f"Binary operator '{node.operator}' cannot consume LIST operands.",
                    )
                return PayloadReturnType.VALUE

        if isinstance(node, LogicalOpNode):
            left_type = self.infer_return_type(
                node.left, source_schema=source_schema, field=f"{field}.left"
            )
            self._ensure_type(
                field=f"{field}.left",
                expected=PayloadReturnType.BOOLEAN,
                got=left_type,
                detail="Logical operators require BOOLEAN operands.",
            )
            if node.right is not None:
                right_type = self.infer_return_type(
                    node.right, source_schema=source_schema, field=f"{field}.right"
                )
                self._ensure_type(
                    field=f"{field}.right",
                    expected=PayloadReturnType.BOOLEAN,
                    got=right_type,
                    detail="Logical operators require BOOLEAN operands.",
                )
            return PayloadReturnType.BOOLEAN

        if isinstance(node, ConditionNode):
            condition_type = self.infer_return_type(
                node.condition,
                source_schema=source_schema,
                field=f"{field}.condition",
            )
            self._ensure_type(
                field=f"{field}.condition",
                expected=PayloadReturnType.BOOLEAN,
                got=condition_type,
                detail="ConditionNode condition must evaluate to BOOLEAN.",
            )
            true_type = self.infer_return_type(
                node.true_expr,
                source_schema=source_schema,
                field=f"{field}.true_expr",
            )
            false_type = self.infer_return_type(
                node.false_expr,
                source_schema=source_schema,
                field=f"{field}.false_expr",
            )
            if true_type != false_type:
                raise RecordValidationError(
                    field=field,
                    expected=true_type.value,
                    got=false_type.value,
                    detail="ConditionNode branches must return the same type.",
                )
            return true_type

        if isinstance(node, DateOpNode):
            if node.left is not None:
                self.infer_return_type(
                    node.left, source_schema=source_schema, field=f"{field}.left"
                )
            if node.right is not None:
                self.infer_return_type(
                    node.right, source_schema=source_schema, field=f"{field}.right"
                )
            return PayloadReturnType.VALUE

        if isinstance(node, StringOpNode):
            self.infer_return_type(
                node.left, source_schema=source_schema, field=f"{field}.left"
            )
            if node.right is not None:
                self.infer_return_type(
                    node.right, source_schema=source_schema, field=f"{field}.right"
                )
            if node.operator == "regex_match":
                return PayloadReturnType.BOOLEAN
            return PayloadReturnType.VALUE

        raise RecordValidationError(
            field=field,
            expected="known AST node",
            got=type(node).__name__,
            detail="Unsupported AST node for trigger type inference.",
        )

    def _infer_field_node(
        self,
        node: FieldNode,
        source_schema: Dict[str, Any],
        field: str,
    ) -> PayloadReturnType:
        return self._return_type_for_field(
            field_name=node.value,
            source_schema=source_schema,
            field=field,
        )

    def _infer_relation_field_node(
        self,
        node: RelationFieldNode,
        source_schema: Dict[str, Any],
        field: str,
    ) -> PayloadReturnType:
        return self._return_type_for_field(
            field_name=node.relation_column,
            source_schema=source_schema,
            field=field,
        )

    def _return_type_for_field(
        self,
        field_name: str,
        source_schema: Dict[str, Any],
        field: str,
    ) -> PayloadReturnType:
        # $old/$new — state-tracking префиксы для UPDATE-событий (ГЗ-2 п.1):
        # тип выводится по базовому полю схемы после префикса.
        if field_name in ("$old", "$new"):
            raise RecordValidationError(
                field=field,
                expected="$old.<field> or $new.<field> path",
                got=field_name,
                detail=(
                    "Bare $old/$new is not allowed in typed expressions — "
                    "reference a concrete field, e.g. $old.status."
                ),
            )
        if field_name.startswith("$old.") or field_name.startswith("$new."):
            field_name = field_name.split(".", 1)[1]

        base_field = field_name.split(".", 1)[0]
        field_meta = source_schema.get(base_field)
        if not field_meta:
            raise RecordValidationError(
                field=field,
                expected="field from source template schema",
                got=field_name,
                detail=f"Field '{field_name}' does not exist in source template schema.",
            )

        field_type = field_meta.get("type")
        if field_type in {"boolean", "checkbox"}:
            return PayloadReturnType.BOOLEAN
        if (
            field_type in self.LIST_FIELD_TYPES
            or field_meta.get("multiple") is True
            or field_meta.get("is_array") is True
        ):
            return PayloadReturnType.LIST
        return PayloadReturnType.VALUE

    def _validate_action_contract(
        self,
        action_name: Optional[str],
        payload_return_type: PayloadReturnType,
        action_mapping_ast: Optional[Dict[str, Any]],
    ) -> None:
        if not action_name:
            return

        signature = get_action_signature(action_name)
        if signature is None:
            raise RecordValidationError(
                field="action_name",
                expected="registered action signature",
                got=action_name,
                detail=f"Action '{action_name}' is not registered in action contracts.",
            )

        if payload_return_type not in signature.accepted_payload_types:
            expected = "|".join(
                sorted(value.value for value in signature.accepted_payload_types)
            )
            raise RecordValidationError(
                field="payload_ast",
                expected=expected,
                got=payload_return_type.value,
                detail=(
                    f"Action '{action_name}' cannot consume "
                    f"{payload_return_type.value} payload."
                ),
            )

        if signature.requires_action_mapping_ast and not action_mapping_ast:
            raise RecordValidationError(
                field="action_mapping_ast",
                expected="non-empty AST mapping",
                got=None,
                detail=f"Action '{action_name}' requires action_mapping_ast.",
            )

    async def _validate_no_cycles(
        self,
        db: AsyncSession,
        instance_uuid: UUID,
        source_template_uuid: UUID,
        target_template_uuid: UUID,
        trigger_uuid: Optional[UUID],
    ) -> None:
        stmt = select(Trigger).where(Trigger.instance_uuid == instance_uuid)
        result = await db.execute(stmt)

        edges: List[Tuple[str, str]] = []
        for trigger in result.scalars().all():
            if trigger_uuid and trigger.id == trigger_uuid:
                continue
            if not self._is_dml_action(trigger.action_name):
                continue
            source_uuid = getattr(trigger, "source_template_uuid", None)
            target_uuid = getattr(trigger, "target_template_uuid", None)
            if source_uuid and target_uuid:
                edges.append((str(source_uuid), str(target_uuid)))

        edges.append((str(source_template_uuid), str(target_template_uuid)))
        cycle_path = self._find_cycle_path(edges)
        if cycle_path:
            rendered_path = " -> ".join(cycle_path)
            raise RecordValidationError(
                field="target_template_uuid",
                expected="acyclic cascade graph",
                got=rendered_path,
                detail=f"Trigger cascade graph contains a cycle: {rendered_path}",
            )

    def _find_cycle_path(self, edges: Iterable[Tuple[str, str]]) -> List[str]:
        graph: Dict[str, List[str]] = {}
        for source, target in edges:
            graph.setdefault(source, []).append(target)
            graph.setdefault(target, [])

        visited: Set[str] = set()
        active: Set[str] = set()
        stack: List[str] = []

        def dfs(node: str) -> List[str]:
            visited.add(node)
            active.add(node)
            stack.append(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    cycle = dfs(neighbor)
                    if cycle:
                        return cycle
                elif neighbor in active:
                    start = stack.index(neighbor)
                    return stack[start:] + [neighbor]

            stack.pop()
            active.remove(node)
            return []

        for node in graph:
            if node not in visited:
                cycle = dfs(node)
                if cycle:
                    return cycle
        return []

    async def _get_template(
        self,
        template_repo: TemplateRepository,
        instance_uuid: UUID,
        template_uuid: UUID,
        field: str,
    ) -> Dict[str, Any]:
        try:
            template = await template_repo.get_template(
                instance_uuid=str(instance_uuid),
                template_uuid=str(template_uuid),
            )
        except TemplateNotFoundError as exc:
            raise RecordValidationError(
                field=field,
                expected=f"template in instance {instance_uuid}",
                got=str(template_uuid),
                detail=exc.message,
            ) from exc

        template_instance_uuid = template.get("instance_uuid")
        if template_instance_uuid and str(template_instance_uuid) != str(instance_uuid):
            raise RecordValidationError(
                field=field,
                expected=f"template in instance {instance_uuid}",
                got=f"template belongs to instance {template_instance_uuid}",
                detail="Cross-tenant template reference is not allowed.",
            )
        return template

    def _parse_tree(self, raw_ast: Any, field: str) -> Any:
        if not raw_ast:
            raise RecordValidationError(
                field=field,
                expected="non-empty AST",
                got=raw_ast,
                detail=f"{field} is required and must be a valid AST object.",
            )
        try:
            return parse_ast(raw_ast)
        except FormulaValidationError as exc:
            raise RecordValidationError(
                field=field,
                expected="valid AST",
                got=raw_ast,
                detail=str(exc),
            ) from exc

    def _require_value(self, trigger_data: Mapping[str, Any], field: str) -> Any:
        value = trigger_data.get(field)
        if value is None:
            raise RecordValidationError(
                field=field,
                expected="required value",
                got=None,
                detail=f"{field} is required for trigger validation.",
            )
        return value

    def _is_dml_action(self, action_name: Optional[str]) -> bool:
        return bool(action_name and action_name in DML_ACTION_NAMES)

    def _validate_dml_target_consistency(
        self,
        target_template_uuid: UUID,
        action_params: Optional[Any],
    ) -> None:
        params = self._action_params_dict(action_params)
        param_target_template_uuid = params.get("target_template_uuid")
        if (
            param_target_template_uuid is not None
            and str(param_target_template_uuid) != str(target_template_uuid)
        ):
            raise RecordValidationError(
                field="action_params.target_template_uuid",
                expected=str(target_template_uuid),
                got=str(param_target_template_uuid),
                detail=(
                    "DML write target is defined exclusively by "
                    "Trigger.target_template_uuid."
                ),
            )

    def _action_params_dict(self, action_params: Optional[Any]) -> Mapping[str, Any]:
        if action_params is None:
            return {}
        if hasattr(action_params, "model_dump"):
            return action_params.model_dump(mode="json")
        if isinstance(action_params, Mapping):
            return action_params
        return {}

    def _ensure_scalar_operand(
        self,
        operand_type: PayloadReturnType,
        field: str,
        operator: str,
    ) -> None:
        if operand_type == PayloadReturnType.LIST:
            raise RecordValidationError(
                field=field,
                expected="scalar operand",
                got=PayloadReturnType.LIST.value,
                detail=f"Binary operator '{operator}' cannot consume LIST operand.",
            )

    def _ensure_type(
        self,
        field: str,
        expected: PayloadReturnType,
        got: PayloadReturnType,
        detail: str,
    ) -> None:
        if got != expected:
            raise RecordValidationError(
                field=field,
                expected=expected.value,
                got=got.value,
                detail=detail,
            )

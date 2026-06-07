from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from engine.atomic_writer import TargetAtomicWriter
from engine.evaluator import ASTEvaluator, EvaluationScope
from engine.iteration_engine import IterationLoopEngine
from engine.ast import parse_ast
from triggers.action_contracts import get_action_signature
from triggers.actions import ACTION_MAPPING
from triggers.exceptions.action import AutomationExecutionError, SystemContractViolation
from triggers.models import EventType, PayloadReturnType, Trigger

CascadeCallback = Callable[
    # (event_type, template_uuid, document, depth, previous_document)
    [EventType, str, Dict[str, Any], int, Optional[Dict[str, Any]]],
    Awaitable[None],
]


class ActionDispatcher:
    """Runtime dispatcher for system and AST-dependent DML actions."""

    DML_ACTIONS = {
        "mongo_insert",
        "INSERT_RECORD",
        "mongo_update",
        "UPDATE_RECORD",
        "mongo_upsert",
        "UPSERT_RECORD",
    }

    INSERT_ACTIONS = {"mongo_insert", "INSERT_RECORD"}
    UPDATE_ACTIONS = {"mongo_update", "UPDATE_RECORD"}
    UPSERT_ACTIONS = {"mongo_upsert", "UPSERT_RECORD"}

    @classmethod
    async def dispatch(
        cls,
        trigger: Trigger,
        action_input: Any,
        scope: EvaluationScope,
        evaluator: ASTEvaluator,
        mongo_db: AsyncIOMotorDatabase,
        pg_session: Any = None,
        cascade_depth: int = 0,
        cascade_callback: Optional[CascadeCallback] = None,
    ) -> Dict[str, Any]:
        action_name = trigger.action_name or "RETURN_TO_CALLER"
        cls._assert_runtime_contract(action_name, action_input)

        if action_name == "RETURN_TO_CALLER":
            return {"status": "success", "result": action_input}

        if action_name in cls.DML_ACTIONS:
            return await cls._dispatch_dml(
                trigger=trigger,
                action_input=action_input,
                scope=scope,
                evaluator=evaluator,
                mongo_db=mongo_db,
                cascade_depth=cascade_depth,
                cascade_callback=cascade_callback,
            )

        action_func = ACTION_MAPPING.get(action_name)
        if not action_func:
            raise SystemContractViolation(
                action_name=action_name,
                expected="runtime action executor",
                got="missing",
            )

        targets = cls._system_targets(action_input)
        result = await action_func(
            instance_uuid=str(trigger.instance_uuid),
            targets=targets,
            params=trigger.action_params or {},
            db=mongo_db,
            pg_session=pg_session,
        )
        return {"status": "success", "result": result}

    @classmethod
    async def _dispatch_dml(
        cls,
        trigger: Trigger,
        action_input: Any,
        scope: EvaluationScope,
        evaluator: ASTEvaluator,
        mongo_db: AsyncIOMotorDatabase,
        cascade_depth: int,
        cascade_callback: Optional[CascadeCallback],
    ) -> Dict[str, Any]:
        target_template_uuid = cls._target_template_uuid(trigger)
        writer = TargetAtomicWriter(
            mongo_db=mongo_db,
            instance_uuid=str(trigger.instance_uuid),
            target_template_uuid=target_template_uuid,
        )

        if isinstance(action_input, list):
            loop_engine = IterationLoopEngine(
                batch_loader=evaluator.batch_loader,
                evaluator=evaluator,
            )

            async def process_item(item: Any, item_scope: EvaluationScope) -> None:
                mapped = await cls._evaluate_mapping(
                    trigger, item, item_scope, evaluator
                )
                cls._enqueue_dml(writer, trigger, mapped, item, item_scope)

            await loop_engine.for_each(
                items=action_input,
                base_scope=scope,
                callback=process_item,
                target_template_uuid=target_template_uuid,
            )
        else:
            mapped = await cls._evaluate_mapping(
                trigger, action_input, scope, evaluator
            )
            cls._enqueue_dml(writer, trigger, mapped, action_input, scope)

        # Pre-images ДО записи: каскадные UPDATE-триггеры получают $old-состояние
        # (иначе пороговые/идемпотентные условия второго звена слепы, ГЗ-2 п.1).
        pre_images = await writer.fetch_pre_images() if cascade_callback else {}

        flush_result = await writer.flush()
        touched_records = await writer.fetch_touched_records()

        if cascade_callback:
            cascade_event = (
                EventType.ON_RECORD_CREATE
                if trigger.action_name in cls.INSERT_ACTIONS
                else EventType.ON_RECORD_UPDATE
            )
            for record in touched_records:
                await cascade_callback(
                    cascade_event,
                    target_template_uuid,
                    record,
                    cascade_depth + 1,
                    pre_images.get(str(record.get("_id"))),
                )

        status = "partial" if flush_result.get("failed_count") else "success"
        return {
            "status": status,
            "write_result": flush_result,
            "touched_records_count": len(touched_records),
        }

    @classmethod
    async def _evaluate_mapping(
        cls,
        trigger: Trigger,
        action_input: Any,
        scope: EvaluationScope,
        evaluator: ASTEvaluator,
    ) -> Dict[str, Any]:
        if trigger.action_mapping_ast:
            mapped = await evaluator.evaluate(
                parse_ast(trigger.action_mapping_ast), scope
            )
        elif isinstance(action_input, dict):
            mapped = action_input.get("data", action_input)
        else:
            mapped = {"value": action_input}

        if isinstance(mapped, dict):
            return mapped
        return {"value": mapped}

    @classmethod
    def _enqueue_dml(
        cls,
        writer: TargetAtomicWriter,
        trigger: Trigger,
        mapped: Dict[str, Any],
        source_item: Any,
        scope: EvaluationScope,
    ) -> None:
        action_name = trigger.action_name or ""
        params = trigger.action_params or {}

        if action_name in cls.INSERT_ACTIONS:
            writer.add_insert(mapped)
            return

        if action_name in cls.UPDATE_ACTIONS:
            record_uuid = (
                params.get("record_uuid")
                or mapped.get("_id")
                or mapped.get("uuid")
                or cls._extract_record_id(source_item)
                or cls._extract_record_id(scope.document)
            )
            search_filter = params.get("filter")
            writer.add_update(
                record_uuid=str(record_uuid) if record_uuid else None,
                operations=mapped,
                search_filter=search_filter,
            )
            return

        if action_name in cls.UPSERT_ACTIONS:
            record_uuid = (
                params.get("record_uuid") or mapped.get("_id") or mapped.get("uuid")
            )
            search_filter = cls._build_search_filter(
                search_fields=params.get("search_fields", []),
                mapped=mapped,
            )
            writer.add_update(
                record_uuid=str(record_uuid) if record_uuid else None,
                operations=mapped,
                upsert=True,
                search_filter=search_filter,
            )
            return

        raise AutomationExecutionError(
            action_name=action_name,
            instance_uuid=str(trigger.instance_uuid),
            reason="DML action is not supported by TargetAtomicWriter.",
        )

    @classmethod
    def _assert_runtime_contract(cls, action_name: str, action_input: Any) -> None:
        signature = get_action_signature(action_name)
        if signature is None:
            raise SystemContractViolation(
                action_name=action_name,
                expected="registered action signature",
                got="missing",
            )

        actual_type = cls._payload_type(action_input)
        if actual_type not in signature.accepted_payload_types:
            expected = "|".join(
                sorted(value.value for value in signature.accepted_payload_types)
            )
            raise SystemContractViolation(
                action_name=action_name,
                expected=expected,
                got=actual_type.value,
            )

    @classmethod
    def _payload_type(cls, value: Any) -> PayloadReturnType:
        if isinstance(value, bool):
            return PayloadReturnType.BOOLEAN
        if isinstance(value, list):
            return PayloadReturnType.LIST
        return PayloadReturnType.VALUE

    @classmethod
    def _target_template_uuid(cls, trigger: Trigger) -> str:
        params = trigger.action_params or {}
        target_template_uuid = trigger.target_template_uuid
        param_target_template_uuid = params.get("target_template_uuid")
        if param_target_template_uuid is not None and str(
            param_target_template_uuid
        ) != str(target_template_uuid):
            raise SystemContractViolation(
                action_name=trigger.action_name or "DML_ACTION",
                expected=str(target_template_uuid),
                got=str(param_target_template_uuid),
            )
        if not target_template_uuid:
            raise AutomationExecutionError(
                action_name=trigger.action_name,
                instance_uuid=str(trigger.instance_uuid),
                reason="DML action requires target_template_uuid.",
            )
        return str(target_template_uuid)

    @classmethod
    def _system_targets(cls, action_input: Any) -> List[Dict[str, Any]]:
        if isinstance(action_input, list):
            return [
                item if isinstance(item, dict) else {"value": item}
                for item in action_input
            ]
        if isinstance(action_input, dict):
            return [action_input]
        return [{"value": action_input}]

    @classmethod
    def _build_search_filter(
        cls, search_fields: Iterable[str], mapped: Dict[str, Any]
    ) -> Dict[str, Any]:
        search_filter = {
            field_name: mapped[field_name]
            for field_name in search_fields
            if field_name in mapped
        }
        if not search_filter:
            raise AutomationExecutionError(
                reason="UPSERT action requires search_fields resolved from mapping.",
            )
        return search_filter

    @classmethod
    def _extract_record_id(cls, item: Any) -> Optional[str]:
        if not item:
            return None
        if isinstance(item, dict):
            raw_id = item.get("_id") or item.get("uuid") or item.get("target_uuid")
            return str(raw_id) if raw_id else None
        return str(item)

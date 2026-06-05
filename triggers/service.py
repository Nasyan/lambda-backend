# triggers/service.py

import logging
from typing import Dict, Any, Optional
from sqlalchemy import select
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import TypeAdapter, ValidationError
from .models import PayloadReturnType, Trigger, EventType, TriggerType
from engine.action_registry import ActionDispatcher
from engine.batch_loader import BatchDataLoader
from engine.evaluator import ASTEvaluator, EvaluationScope
from engine.event_receptor import EventReceptor
from engine.ast import ASTNode, parse_ast
from engine.evaluator import FormulaEvaluator
from mongo.template import TemplateRepository

# Импортируем наши профессиональные исключения
from triggers.exceptions.action import AutomationExecutionError, SystemContractViolation
from triggers.exceptions.service import (
    AutomationConditionEvaluationError,
)

logger = logging.getLogger(__name__)


class AutomationService:
    MAX_CASCADE_DEPTH = 5

    @classmethod
    async def handle_event(
        cls,
        pg_session: Any,
        mongo_db: AsyncIOMotorDatabase,
        instance_uuid: str,
        template_uuid: str,
        event_type: EventType,
        document: Dict[str, Any],
        manual_input: Optional[Any] = None,
        cascade_depth: int = 0,
    ) -> Dict[str, Any]:
        if cascade_depth > cls.MAX_CASCADE_DEPTH:
            raise AutomationExecutionError(
                action_name="trigger_cascade",
                instance_uuid=str(instance_uuid),
                reason=(
                    f"Превышена глубина каскада триггеров: {cascade_depth}."
                ),
            )

        pg_session = await cls._ensure_session(pg_session)
        normalized_event = cls._normalize_event_type(event_type)
        receptor = EventReceptor(
            pg_session=pg_session,
            mongo_db=mongo_db,
        )
        event_context = await receptor.capture(
            event_type=normalized_event,
            instance_uuid=str(instance_uuid),
            template_uuid=str(template_uuid),
            document=document,
            manual_input=manual_input,
        )

        results = []

        async def cascade_callback(
            nested_event_type: EventType,
            nested_template_uuid: str,
            nested_document: Dict[str, Any],
            nested_depth: int,
        ) -> None:
            await cls.handle_event(
                pg_session=pg_session,
                mongo_db=mongo_db,
                instance_uuid=str(instance_uuid),
                template_uuid=str(nested_template_uuid),
                event_type=nested_event_type,
                document=nested_document,
                manual_input=manual_input,
                cascade_depth=nested_depth,
            )

        for trigger in event_context.triggers:
            result = await cls._run_trigger_pipeline(
                trigger=trigger,
                event_scope=event_context.scope,
                data_loader=event_context.data_loader,
                mongo_db=mongo_db,
                pg_session=pg_session,
                cascade_depth=cascade_depth,
                cascade_callback=cascade_callback,
            )
            results.append({"trigger_id": str(trigger.id), "result": result})

        return {"status": "success", "trigger_results": results}

    @classmethod
    async def evaluate_trigger_payload(
        cls,
        mongo_db: AsyncIOMotorDatabase,
        instance_uuid: str,
        trigger: Trigger,
        context_data: Dict[str, Any],
        manual_input: Optional[Any] = None,
    ) -> Any:
        template_repo = TemplateRepository(mongo_db)
        template = await template_repo.get_template(
            instance_uuid=str(instance_uuid),
            template_uuid=str(trigger.source_template_uuid),
        )
        data_loader = BatchDataLoader(mongo_db=mongo_db, instance_uuid=str(instance_uuid))
        evaluator = ASTEvaluator(batch_loader=data_loader)
        variables = {}
        if manual_input is not None:
            variables["__input_value__"] = manual_input
            variables["input"] = manual_input
        scope = EvaluationScope(
            document=context_data,
            instance_uuid=str(instance_uuid),
            variables=variables,
            source_schema=template.get("schema", {}),
        )
        if trigger.condition_ast:
            condition_value = await evaluator.evaluate(
                parse_ast(trigger.condition_ast),
                scope,
            )
            if condition_value is not True:
                payload_return_type = trigger.payload_return_type
                if hasattr(payload_return_type, "value"):
                    payload_return_type = payload_return_type.value
                if payload_return_type == PayloadReturnType.LIST.value:
                    return []
                return None
        return await evaluator.evaluate(parse_ast(trigger.payload_ast), scope)

    @classmethod
    async def execute_trigger_once(
        cls,
        pg_session: Any,
        mongo_db: AsyncIOMotorDatabase,
        trigger: Trigger,
        document: Dict[str, Any],
        manual_input: Optional[Any] = None,
        cascade_depth: int = 0,
    ) -> Dict[str, Any]:
        template_repo = TemplateRepository(mongo_db)
        template = await template_repo.get_template(
            instance_uuid=str(trigger.instance_uuid),
            template_uuid=str(trigger.source_template_uuid),
        )
        data_loader = BatchDataLoader(
            mongo_db=mongo_db,
            instance_uuid=str(trigger.instance_uuid),
        )
        variables = {}
        if manual_input is not None:
            variables["__input_value__"] = manual_input
            variables["input"] = manual_input
        scope = EvaluationScope(
            document=document or {},
            instance_uuid=str(trigger.instance_uuid),
            variables=variables,
            source_schema=template.get("schema", {}),
        )

        async def cascade_callback(
            nested_event_type: EventType,
            nested_template_uuid: str,
            nested_document: Dict[str, Any],
            nested_depth: int,
        ) -> None:
            await cls.handle_event(
                pg_session=pg_session,
                mongo_db=mongo_db,
                instance_uuid=str(trigger.instance_uuid),
                template_uuid=nested_template_uuid,
                event_type=nested_event_type,
                document=nested_document,
                manual_input=manual_input,
                cascade_depth=nested_depth,
            )

        return await cls._run_trigger_pipeline(
            trigger=trigger,
            event_scope=scope,
            data_loader=data_loader,
            mongo_db=mongo_db,
            pg_session=pg_session,
            cascade_depth=cascade_depth,
            cascade_callback=cascade_callback,
        )

    @classmethod
    async def _run_trigger_pipeline(
        cls,
        trigger: Trigger,
        event_scope: EvaluationScope,
        data_loader: BatchDataLoader,
        mongo_db: AsyncIOMotorDatabase,
        pg_session: Any,
        cascade_depth: int,
        cascade_callback: Any,
    ) -> Dict[str, Any]:
        evaluator = ASTEvaluator(batch_loader=data_loader)
        if trigger.condition_ast:
            condition_value = await evaluator.evaluate(
                parse_ast(trigger.condition_ast),
                event_scope,
            )
            if condition_value is not True:
                return {"status": "skipped", "reason": "condition_false"}

        payload = await evaluator.evaluate(parse_ast(trigger.payload_ast), event_scope)
        cls._assert_declared_payload_type(trigger, payload)

        return await ActionDispatcher.dispatch(
            trigger=trigger,
            action_input=payload,
            scope=event_scope,
            evaluator=evaluator,
            mongo_db=mongo_db,
            pg_session=pg_session,
            cascade_depth=cascade_depth,
            cascade_callback=cascade_callback,
        )

    @classmethod
    async def execute_automation_triggers(
        cls,
        pg_session: Any,
        mongo_db: AsyncIOMotorDatabase,
        instance_uuid: str,
        template_uuid: str,
        event_type: EventType,
        current_record: Dict[str, Any],
    ) -> None:
        """
        Ищет активные триггеры автоматизации в Postgres и последовательно выполняет их.
        """
        await cls.handle_event(
            pg_session=pg_session,
            mongo_db=mongo_db,
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            event_type=event_type,
            document=current_record,
        )

    @classmethod
    async def _evaluate_condition(
        cls,
        trigger_name: str,
        ast_condition: Optional[Dict[str, Any]],
        record: Dict[str, Any],
    ) -> bool:
        """
        Полноценный мост между сырым JSON из БД и движком вычисления формул.
        """
        if not ast_condition or ast_condition == {}:
            return True  # Если условие пустое — выполняем безусловно

        try:
            # Валидируем сырой словарь в полиморфное Pydantic-дерево
            ast_node = TypeAdapter(ASTNode).validate_python(ast_condition)

            # Прокидываем в эвалюатор. Текущая запись становится контекстом.
            result = await FormulaEvaluator.evaluate(node=ast_node, context=record)

            return bool(result)

        except ValidationError as e:
            raise AutomationConditionEvaluationError(
                trigger_name=trigger_name,
                reason="Структура AST-дерева в базе данных повреждена или не валидна.",
                details={"errors": e.errors()},
            )
        except Exception as e:
            raise AutomationConditionEvaluationError(
                trigger_name=trigger_name,
                reason=f"Внутренний сбой эвалюатора формул: {str(e)}",
            )

    @classmethod
    async def process_cron_triggers(
        cls, pg_session: Any, mongo_db: AsyncIOMotorDatabase
    ) -> None:
        """
        Сканирует Postgres на наличие временных (CRON) триггеров автоматизации,
        выбирает целевые записи из Mongo и выполняет условия.
        """
        stmt = select(Trigger).where(
            Trigger.trigger_type == TriggerType.AUTOMATION,
            Trigger.event_type == EventType.CRON,
        )

        if hasattr(pg_session, "execute"):
            result = await pg_session.execute(stmt)
            cron_triggers = result.scalars().all()
        else:
            cron_triggers = pg_session.scalars(stmt).all()

        if not cron_triggers:
            logger.debug("[CRON] Активных временных триггеров в БД не обнаружено.")
            return

        for trigger in cron_triggers:
            if not trigger.source_template_uuid:
                continue

            template_uuid_str = str(trigger.source_template_uuid)
            instance_uuid_str = str(trigger.instance_uuid)

            # Оптимизация: Запрашиваем только нужные поля, если это возможно,
            # но пока оставляем find для полной совместимости с контекстом
            cursor = mongo_db["records"].find(
                {"template_uuid": template_uuid_str, "instance_uuid": instance_uuid_str}
            )

            async for record in cursor:
                # 🔥 ТОЧКА ИЗОЛЯЦИИ: Создаем SAVEPOINT или управляем commit/rollback поштучно
                try:
                    is_valid = await cls._evaluate_condition(
                        trigger.name, trigger.condition_ast, record
                    )

                    if not is_valid:
                        continue

                    await cls._run_trigger_pipeline(
                        trigger=trigger,
                        event_scope=EvaluationScope(
                            document=record,
                            instance_uuid=instance_uuid_str,
                        ),
                        data_loader=BatchDataLoader(
                            mongo_db=mongo_db,
                            instance_uuid=instance_uuid_str,
                        ),
                        mongo_db=mongo_db,
                        pg_session=pg_session,
                        cascade_depth=0,
                        cascade_callback=None,
                    )

                    # 🔥 ФИКС: Коммитим Postgres транзакцию строго для ТЕКУЩЕЙ успешной записи
                    if hasattr(pg_session, "commit"):
                        await pg_session.commit()

                    logger.info(
                        f"[CRON SUCCESS] Триггер '{trigger.name}' успешно обработал запись {record.get('_id')}"
                    )

                except SystemContractViolation as e:
                    if hasattr(pg_session, "rollback"):
                        await pg_session.rollback()

                    logger.error(
                        f"[CRON CONTRACT ERROR] Системный контракт нарушен "
                        f"в триггере '{trigger.name}': {str(e)}",
                        exc_info=True,
                    )
                    raise
                except Exception as e:
                    # Если упала конкретная запись — откатываем только её операции в PG
                    if hasattr(pg_session, "rollback"):
                        await pg_session.rollback()

                    logger.error(
                        f"[CRON RECORD ERROR] Ошибка обработки записи {record.get('_id')} "
                        f"в триггере '{trigger.name}': {str(e)}",
                        exc_info=True,
                    )
                    # Проглатываем ошибку (continue), переходим к следующему документу таблицы!
                    continue

    @classmethod
    async def _ensure_session(cls, pg_session: Any) -> Any:
        if hasattr(pg_session, "__anext__"):
            async for session in pg_session:
                return session
        return pg_session

    @classmethod
    def _normalize_event_type(cls, event_type: Any) -> EventType:
        if isinstance(event_type, EventType):
            return event_type
        return EventType(str(event_type))

    @classmethod
    def _assert_declared_payload_type(cls, trigger: Trigger, payload: Any) -> None:
        actual_type = cls._payload_type(payload)
        declared_type = trigger.payload_return_type
        if hasattr(declared_type, "value"):
            declared_type = declared_type.value
        if declared_type != actual_type.value:
            raise SystemContractViolation(
                action_name=trigger.action_name or "RETURN_TO_CALLER",
                expected=str(declared_type),
                got=actual_type.value,
            )

    @classmethod
    def _payload_type(cls, payload: Any):
        from triggers.models import PayloadReturnType

        if isinstance(payload, bool):
            return PayloadReturnType.BOOLEAN
        if isinstance(payload, list):
            return PayloadReturnType.LIST
        return PayloadReturnType.VALUE

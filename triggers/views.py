# triggers/views.py

import logging
from uuid import UUID
from typing import List, Dict, Any
import uuid
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.db import get_db
from triggers.models import Trigger
from triggers.schemas import TriggerCreate, TriggerResponse, TriggerEvaluateRequest

from users.models import Users, AppTools, UserRole
from jsonwebtoken.utils import get_current_active_user
from users.auth import RequireTool

from mongo.dependecies import get_record_repository, get_template_repository
from mongo.record import RecordRepository
from mongo.template import TemplateRepository

from engine.evaluator import FormulaEvaluator
from engine.context import RecordResolverSession
from engine.ast import parse_ast
from triggers.actions import ACTION_MAPPING

from core.exceptions.dependecies import (
    CreatorRoleRequiredError,
    InstanceAccessDeniedError,
    InstanceNotFoundError,
)
from triggers.exceptions.action import (
    AutomationValidationError,
    AutomationExecutionError,
    TriggerNotFoundDomainError,
)

from middleware.schemas import ListParameters

from engine.exceptions.evaluator import FormulaEvaluationError
from engine.integrity import SchemaIntegrityValidator

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/instances/{instance_uuid}/triggers",
    tags=["Triggers"],
    dependencies=[Depends(RequireTool(AppTools.TRIGGERS))],
)


def verify_creator_and_instance(instance_uuid: UUID, current_user: Users) -> None:
    """
    Бизнес-валидация прав Креатора и изоляция инстанса через доменные ошибки.
    """
    if current_user.role != UserRole.CREATOR:
        raise CreatorRoleRequiredError()

    if not current_user.instance_id:
        raise InstanceNotFoundError(
            detail="Creator account is not associated with any active instance."
        )

    if current_user.instance_id != instance_uuid:
        # ПЕРЕДАЕМ ОБЯЗАТЕЛЬНЫЕ АРГУМЕНТЫ СЮДА:
        raise InstanceAccessDeniedError(
            user_uuid=str(current_user.uuid),
            user_instance_id=str(current_user.instance_id),
            target_instance_uuid=str(instance_uuid),
        )


@router.post("/", response_model=TriggerResponse, status_code=status.HTTP_201_CREATED)
async def create_trigger(
    instance_uuid: UUID,
    payload: TriggerCreate,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    template_repo: TemplateRepository = Depends(get_template_repository),
):
    verify_creator_and_instance(instance_uuid, current_user)

    # Валидация AST графа
    try:
        parse_ast(payload.ast)
    except Exception as e:
        raise AutomationValidationError(detail=f"Кривой AST граф: {str(e)}")

    if payload.target_template_uuid:
        await SchemaIntegrityValidator.validate_trigger_ast_fields(
            instance_uuid=instance_uuid,
            template_uuid=payload.target_template_uuid,
            ast=payload.ast,
            template_repo=template_repo,
        )

    target_field = getattr(payload, "target_field", None)

    db_trigger = Trigger(
        id=uuid.uuid4(),
        instance_uuid=instance_uuid,
        name=payload.name,
        trigger_type=payload.trigger_type,
        ast=payload.ast,
        target_template_uuid=payload.target_template_uuid,
        target_field=target_field,
        event_type=payload.event_type,
        action_name=payload.action_name,
        action_params=(
            payload.action_params.model_dump()
            if hasattr(payload.action_params, "model_dump")
            else payload.action_params
        ),
    )
    db.add(db_trigger)
    await db.flush()

    # Инжекция триггера в динамическую схему Монго
    if target_field and payload.target_template_uuid:
        trigger_data = {
            "trigger_id": str(db_trigger.id),
            "trigger_type": db_trigger.trigger_type,
            "event": db_trigger.event_type or "onCalculate",
            "target_field": target_field,
        }

        await template_repo.inject_trigger_to_schema(
            instance_uuid=str(instance_uuid),
            template_uuid=str(payload.target_template_uuid),
            column_name=target_field,
            trigger_data=trigger_data,
            user_uuid=str(current_user.uuid),
        )

    await db.commit()
    await db.refresh(db_trigger)
    return db_trigger


@router.get("/", response_model=List[TriggerResponse])
async def get_triggers(
    instance_uuid: UUID,
    params: ListParameters = Depends(),  # <-- Внедряем контракт параметров!
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    verify_creator_and_instance(instance_uuid, current_user)

    # 1. Базовый запрос с фильтрацией по тенанту (мультитенантность)
    stmt = select(Trigger).where(Trigger.instance_uuid == instance_uuid)

    # 2. Динамически добавляем поиск (PostgreSQL ILIKE — регистронезависимый поиск)
    if params.search:
        # Например, ищем по имени триггера (Trigger.name)
        stmt = stmt.where(Trigger.name.ilike(f"%{params.search}%"))

    # 3. Динамически добавляем сортировку, передавая модель Trigger в наш хелпер
    sort_criterion = params.get_postgres_sort(model=Trigger, default_field="created_at")
    stmt = stmt.order_by(sort_criterion)

    # 4. Выполняем запрос
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/{trigger_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trigger(
    instance_uuid: UUID,
    trigger_uuid: UUID,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    template_repo: TemplateRepository = Depends(get_template_repository),
):
    verify_creator_and_instance(instance_uuid, current_user)

    result = await db.execute(
        select(Trigger).where(
            Trigger.instance_uuid == instance_uuid,
            Trigger.id == trigger_uuid,
        )
    )
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise TriggerNotFoundDomainError(trigger_uuid=str(trigger_uuid))

    target_field = getattr(trigger, "target_field", None)

    if trigger.target_template_uuid and target_field:
        await template_repo.remove_trigger_from_schema(
            instance_uuid=str(instance_uuid),
            template_uuid=str(trigger.target_template_uuid),
            column_name=target_field,
            trigger_id=str(trigger.id),
            user_uuid=str(current_user.id),
        )

    await db.delete(trigger)
    await db.commit()


@router.post("/{trigger_uuid}/evaluate")
async def evaluate_trigger_live(
    instance_uuid: UUID,
    trigger_uuid: UUID,
    payload: TriggerEvaluateRequest,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    mongo_repo: RecordRepository = Depends(get_record_repository),
):
    verify_creator_and_instance(instance_uuid, current_user)

    result = await db.execute(
        select(Trigger).where(
            Trigger.instance_uuid == instance_uuid,
            Trigger.id == trigger_uuid,
        )
    )
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise TriggerNotFoundDomainError(trigger_uuid=str(trigger_uuid))

    try:
        ast_tree = parse_ast(trigger.ast)
    except Exception as e:
        raise AutomationValidationError(detail=f"Ошибка парсинга AST: {str(e)}")

    # Локальные фабрики контекстов
    async def batch_fetcher(uuids: List[str]) -> Dict[str, Dict[str, Any]]:
        return await mongo_repo.get_records_by_uuids(str(instance_uuid), uuids)

    session_resolver = RecordResolverSession(batch_fetch_func=batch_fetcher)

    async def resolve_aggregation(
        target_template_uuid: str,
        filter_field: str,
        filter_value: Any,
        agg_function: str,
        agg_field: str,
    ):
        return await mongo_repo.aggregate_records(
            instance_uuid=str(instance_uuid),
            target_template_uuid=target_template_uuid,
            filter_field=filter_field,
            filter_value=filter_value,
            agg_function=agg_function,
            agg_field=agg_field,
        )

    try:
        result_value = await FormulaEvaluator.evaluate(
            node=ast_tree,
            context=payload.context_data,
            record_resolver=session_resolver,
            aggregation_resolver=resolve_aggregation,
        )
        return {"status": "success", "result": result_value}
    except Exception as e:
        raise FormulaEvaluationError(f"Ошибка рантайма формулы: {str(e)}")


@router.post("/{trigger_uuid}/execute")
async def execute_trigger_action(
    instance_uuid: UUID,
    trigger_uuid: UUID,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    mongo_repo: RecordRepository = Depends(get_record_repository),
):
    verify_creator_and_instance(instance_uuid, current_user)

    result = await db.execute(
        select(Trigger).where(
            Trigger.instance_uuid == instance_uuid,
            Trigger.id == trigger_uuid,
        )
    )
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise TriggerNotFoundDomainError(trigger_uuid=str(trigger_uuid))

    trigger_type_value = (
        trigger.trigger_type.value
        if hasattr(trigger.trigger_type, "value")
        else trigger.trigger_type
    )

    if trigger_type_value != "AUTOMATION" or not trigger.action_name:
        raise AutomationValidationError(
            detail="Этот триггер не является автоматизацией."
        )

    action_func = ACTION_MAPPING.get(trigger.action_name)
    if not action_func:
        raise AutomationValidationError(
            detail=f"Неизвестное действие: {trigger.action_name}"
        )

    try:
        ast_tree = parse_ast(trigger.ast)
    except Exception as e:
        raise AutomationValidationError(detail=f"Ошибка парсинга AST условия: {str(e)}")

    if not trigger.target_template_uuid:
        raise AutomationValidationError(
            detail="Триггер должен быть привязан к target_template_uuid"
        )

    # Сборка контекста вычислений
    async def batch_fetcher(uuids: List[str]) -> Dict[str, Dict[str, Any]]:
        return await mongo_repo.get_records_by_uuids(str(instance_uuid), uuids)

    session_resolver = RecordResolverSession(batch_fetch_func=batch_fetcher)

    async def resolve_aggregation(
        target_template_uuid: str,
        filter_field: str,
        filter_value: Any,
        agg_function: str,
        agg_field: str,
    ):
        return await mongo_repo.aggregate_records(
            instance_uuid=str(instance_uuid),
            target_template_uuid=target_template_uuid,
            filter_field=filter_field,
            filter_value=filter_value,
            agg_function=agg_function,
            agg_field=agg_field,
        )

    matched_targets = []
    async for record in mongo_repo.stream_records(
        instance_uuid=str(instance_uuid),
        template_uuid=str(trigger.target_template_uuid),
    ):
        try:
            is_match = await FormulaEvaluator.evaluate(
                node=ast_tree,
                context=record,
                record_resolver=session_resolver,
                aggregation_resolver=resolve_aggregation,
            )
            if is_match is True:
                matched_targets.append(record)
        except Exception as e:
            logger.warning(
                f"Ошибка вычисления условия для записи {record.get('_id')}: {e}"
            )
            continue

    try:
        mongo_db_instance = mongo_repo.collection.database
        execution_result = await action_func(
            instance_uuid=str(instance_uuid),
            targets=matched_targets,
            params=trigger.action_params or {},
            db=mongo_db_instance,
        )

        return {
            "status": "success",
            "message": f"Действие {trigger.action_name} успешно выполнено.",
            "matched_records_count": len(matched_targets),
            "execution_details": execution_result,
        }
    except Exception as e:
        raise AutomationExecutionError(
            detail=f"Ошибка выполнения действия автоматизации: {str(e)}"
        )

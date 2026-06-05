# triggers/views.py

from uuid import UUID
from typing import List, Dict, Any
import uuid
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.db import get_db
from triggers.models import Trigger
from triggers.schemas import (
    TriggerCreate,
    TriggerEvaluateRequest,
    TriggerResponse,
    TriggerUpdate,
)
from triggers.service import AutomationService
from triggers.validator import TriggerSchemaValidator

from users.models import Users, AppTools, UserRole
from jsonwebtoken.utils import get_current_active_user
from users.auth import RequireTool

from mongo.dependecies import get_record_repository, get_template_repository
from mongo.record import RecordRepository
from mongo.template import TemplateRepository

from core.exceptions.dependecies import (
    CreatorRoleRequiredError,
    InstanceAccessDeniedError,
    InstanceNotFoundError,
)
from triggers.exceptions.action import (
    AutomationValidationError,
    AutomationExecutionError,
    SystemContractViolation,
    TriggerNotFoundDomainError,
)

from middleware.schemas import ListParameters

from engine.exceptions.evaluator import FormulaEvaluationError

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


def _dump_action_params(action_params: Any) -> Any:
    if hasattr(action_params, "model_dump"):
        return action_params.model_dump(mode="json")
    return action_params


def _trigger_validation_data(trigger: Trigger) -> Dict[str, Any]:
    return {
        "instance_uuid": trigger.instance_uuid,
        "name": trigger.name,
        "trigger_type": trigger.trigger_type,
        "condition_ast": trigger.condition_ast,
        "payload_ast": trigger.payload_ast,
        "payload_return_type": trigger.payload_return_type,
        "action_mapping_ast": trigger.action_mapping_ast,
        "source_template_uuid": trigger.source_template_uuid,
        "target_template_uuid": trigger.target_template_uuid,
        "target_field": trigger.target_field,
        "event_type": trigger.event_type,
        "cron_expression": trigger.cron_expression,
        "action_name": trigger.action_name,
        "action_params": trigger.action_params,
    }


@router.post("/", response_model=TriggerResponse, status_code=status.HTTP_201_CREATED)
async def create_trigger(
    instance_uuid: UUID,
    payload: TriggerCreate,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    template_repo: TemplateRepository = Depends(get_template_repository),
):
    verify_creator_and_instance(instance_uuid, current_user)

    validator = TriggerSchemaValidator()
    trigger_data = payload.model_dump()
    trigger_data["instance_uuid"] = instance_uuid
    trigger_data["action_params"] = _dump_action_params(payload.action_params)
    payload_return_type = await validator.validate(
        trigger_data=trigger_data,
        db=db,
        template_repo=template_repo,
    )

    target_field = getattr(payload, "target_field", None)

    db_trigger = Trigger(
        id=uuid.uuid4(),
        instance_uuid=instance_uuid,
        name=payload.name,
        trigger_type=payload.trigger_type,
        condition_ast=payload.condition_ast,
        payload_ast=payload.payload_ast,
        payload_return_type=payload_return_type,
        action_mapping_ast=payload.action_mapping_ast,
        source_template_uuid=payload.source_template_uuid,
        target_template_uuid=payload.target_template_uuid,
        target_field=target_field,
        event_type=payload.event_type,
        cron_expression=payload.cron_expression,
        action_name=payload.action_name,
        action_params=_dump_action_params(payload.action_params),
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


@router.patch("/{trigger_uuid}", response_model=TriggerResponse)
async def update_trigger(
    instance_uuid: UUID,
    trigger_uuid: UUID,
    payload: TriggerUpdate,
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

    update_data = payload.model_dump(exclude_unset=True)
    if "action_params" in update_data:
        update_data["action_params"] = _dump_action_params(payload.action_params)

    validation_data = _trigger_validation_data(trigger)
    validation_data.update(update_data)
    validation_data["instance_uuid"] = instance_uuid

    validator = TriggerSchemaValidator()
    payload_return_type = await validator.validate(
        trigger_data=validation_data,
        db=db,
        template_repo=template_repo,
        trigger_uuid=trigger_uuid,
    )

    old_target_field = trigger.target_field
    old_target_template_uuid = trigger.target_template_uuid
    should_sync_schema = bool(
        {"target_field", "target_template_uuid", "trigger_type", "event_type"}
        & set(update_data.keys())
    )

    for field_name, value in update_data.items():
        setattr(trigger, field_name, value)
    trigger.payload_return_type = payload_return_type

    if should_sync_schema and old_target_field and old_target_template_uuid:
        await template_repo.remove_trigger_from_schema(
            instance_uuid=str(instance_uuid),
            template_uuid=str(old_target_template_uuid),
            column_name=old_target_field,
            trigger_id=str(trigger.id),
            user_uuid=str(current_user.uuid),
        )

    if should_sync_schema and trigger.target_field and trigger.target_template_uuid:
        trigger_data_for_schema = {
            "trigger_id": str(trigger.id),
            "trigger_type": trigger.trigger_type,
            "event": trigger.event_type or "onCalculate",
            "target_field": trigger.target_field,
        }

        await template_repo.inject_trigger_to_schema(
            instance_uuid=str(instance_uuid),
            template_uuid=str(trigger.target_template_uuid),
            column_name=trigger.target_field,
            trigger_data=trigger_data_for_schema,
            user_uuid=str(current_user.uuid),
        )

    await db.commit()
    await db.refresh(trigger)
    return trigger


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
        result_value = await AutomationService.evaluate_trigger_payload(
            mongo_db=mongo_repo.collection.database,
            instance_uuid=str(instance_uuid),
            trigger=trigger,
            context_data=payload.context_data,
            manual_input=payload.manual_input,
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

    trigger_type_value = trigger.trigger_type.value if hasattr(
        trigger.trigger_type, "value"
    ) else trigger.trigger_type

    if trigger_type_value != "AUTOMATION":
        raise AutomationValidationError(
            detail="Этот триггер не является автоматизацией."
        )

    try:
        execution_result = await AutomationService.execute_trigger_once(
            pg_session=db,
            mongo_db=mongo_repo.collection.database,
            trigger=trigger,
            document={},
        )
        return {
            "status": "success",
            "message": f"Триггер {trigger.name} успешно выполнен.",
            "execution_details": execution_result,
        }
    except SystemContractViolation:
        raise
    except Exception as e:
        raise AutomationExecutionError(
            detail=(
                f"Ошибка выполнения действия автоматизации: {str(e)}"
            )
        )

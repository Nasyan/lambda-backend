# triggers/views.py

"""Тонкий роутер триггеров (task3, ГЗ-1 Этап 2).

Только приём HTTP-запроса, базовые права и передача DTO в
TriggerAdminService / AutomationService. SQL и синхронизация
Mongo-метаданных живут в сервисном слое и репозиториях.
"""

from uuid import UUID
from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from triggers.schemas import (
    TriggerCreate,
    TriggerEvaluateRequest,
    TriggerResponse,
    TriggerUpdate,
)
from triggers.admin_service import TriggerAdminService
from triggers.service import AutomationService

from users.models import Users, AppTools, UserRole
from jsonwebtoken.utils import get_current_active_user
from users.auth import RequireTool

from mongo.dependecies import (
    get_record_repository,
    get_template_repository,
    get_trigger_metadata_repository,
)
from mongo.record import RecordRepository
from mongo.template import TemplateRepository
from mongo.trigger_metadata import TriggerMetadataRepository

from core.exceptions.dependecies import (
    CreatorRoleRequiredError,
    InstanceAccessDeniedError,
    InstanceNotFoundError,
)
from triggers.exceptions.action import (
    AutomationValidationError,
    AutomationExecutionError,
    SystemContractViolation,
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


def get_trigger_admin_service(
    db: AsyncSession = Depends(get_db),
    template_repo: TemplateRepository = Depends(get_template_repository),
    trigger_meta_repo: TriggerMetadataRepository = Depends(
        get_trigger_metadata_repository
    ),
) -> TriggerAdminService:
    return TriggerAdminService(
        db=db,
        template_repo=template_repo,
        trigger_meta_repo=trigger_meta_repo,
    )


@router.post("/", response_model=TriggerResponse, status_code=status.HTTP_201_CREATED)
async def create_trigger(
    instance_uuid: UUID,
    payload: TriggerCreate,
    current_user: Users = Depends(get_current_active_user),
    admin_service: TriggerAdminService = Depends(get_trigger_admin_service),
):
    verify_creator_and_instance(instance_uuid, current_user)

    return await admin_service.create_trigger(
        instance_uuid=instance_uuid,
        payload=payload,
        user_uuid=current_user.uuid,
    )


@router.patch("/{trigger_uuid}", response_model=TriggerResponse)
async def update_trigger(
    instance_uuid: UUID,
    trigger_uuid: UUID,
    payload: TriggerUpdate,
    current_user: Users = Depends(get_current_active_user),
    admin_service: TriggerAdminService = Depends(get_trigger_admin_service),
):
    verify_creator_and_instance(instance_uuid, current_user)

    return await admin_service.update_trigger(
        instance_uuid=instance_uuid,
        trigger_uuid=trigger_uuid,
        payload=payload,
        user_uuid=current_user.uuid,
    )


@router.get("/", response_model=List[TriggerResponse])
async def get_triggers(
    instance_uuid: UUID,
    params: ListParameters = Depends(),  # <-- Внедряем контракт параметров!
    current_user: Users = Depends(get_current_active_user),
    admin_service: TriggerAdminService = Depends(get_trigger_admin_service),
):
    verify_creator_and_instance(instance_uuid, current_user)

    return await admin_service.list_triggers(instance_uuid, params)


@router.delete("/{trigger_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trigger(
    instance_uuid: UUID,
    trigger_uuid: UUID,
    current_user: Users = Depends(get_current_active_user),
    admin_service: TriggerAdminService = Depends(get_trigger_admin_service),
):
    verify_creator_and_instance(instance_uuid, current_user)

    await admin_service.delete_trigger(
        instance_uuid=instance_uuid,
        trigger_uuid=trigger_uuid,
        user_uuid=getattr(current_user, "id", None) or current_user.uuid,
    )


@router.post("/{trigger_uuid}/evaluate")
async def evaluate_trigger_live(
    instance_uuid: UUID,
    trigger_uuid: UUID,
    payload: TriggerEvaluateRequest,
    current_user: Users = Depends(get_current_active_user),
    admin_service: TriggerAdminService = Depends(get_trigger_admin_service),
    mongo_repo: RecordRepository = Depends(get_record_repository),
):
    verify_creator_and_instance(instance_uuid, current_user)

    trigger = await admin_service.get_trigger_or_raise(instance_uuid, trigger_uuid)

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
    admin_service: TriggerAdminService = Depends(get_trigger_admin_service),
    mongo_repo: RecordRepository = Depends(get_record_repository),
    db: AsyncSession = Depends(get_db),
):
    verify_creator_and_instance(instance_uuid, current_user)

    trigger = await admin_service.get_trigger_or_raise(instance_uuid, trigger_uuid)

    trigger_type_value = (
        trigger.trigger_type.value
        if hasattr(trigger.trigger_type, "value")
        else trigger.trigger_type
    )

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
            detail=(f"Ошибка выполнения действия автоматизации: {str(e)}")
        )

# instance_schema/views.py

"""HTTP-слой выгрузки/загрузки схемы инстанса (задание 4, 2026-06-10)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependencies import get_current_instance_creator
from database.db import get_db
from instance_schema.schemas import ImportReport, ImportRequest, InstanceSchemaBundle
from instance_schema.service import InstanceSchemaService
from jsonwebtoken.utils import get_current_user
from mongo.db import get_mongo_db
from users.models import Instances, Users

router = APIRouter(
    prefix="/instances/{instance_uuid}/schema",
    tags=["Instance Schema"],
)


def get_instance_schema_service(
    db: AsyncSession = Depends(get_db),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> InstanceSchemaService:
    return InstanceSchemaService(db=db, mongo_db=mongo_db)


@router.get(
    "/export", response_model=InstanceSchemaBundle, response_model_by_alias=True
)
async def export_instance_schema(
    instance_uuid: UUID,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    service: InstanceSchemaService = Depends(get_instance_schema_service),
):
    """Выгружает ВСЮ конфигурацию инстанса одним JSON: templates, triggers,
    widgets, policies, notification templates. Records (данные) не входят."""
    return await service.export_schema(instance.uuid)


@router.post("/import", response_model=ImportReport)
async def import_instance_schema(
    instance_uuid: UUID,
    payload: ImportRequest,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    service: InstanceSchemaService = Depends(get_instance_schema_service),
):
    """Загружает bundle конфигурации.

    Режимы: merge (поверх существующего) / replace (снести текущую конфигурацию
    и загрузить bundle; в ответе previous_schema для отката тем же эндпоинтом).
    dry_run=true — только проверка целостности + план порядка применения.
    Невалидный bundle → 422, ничего не применяется.
    """
    report = await service.import_schema(
        instance_uuid=instance.uuid,
        bundle=payload.bundle,
        mode=payload.mode,
        user_uuid=current_user.uuid,
        dry_run=payload.dry_run,
    )
    if not report.valid and not report.created:
        # Валидация не прошла — изменений не было
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=report.model_dump(mode="json"),
        )
    return report

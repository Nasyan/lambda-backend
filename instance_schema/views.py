# instance_schema/views.py
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from instance_schema.schemas import ImportReport, ImportRequest, InstanceSchemaBundle
from instance_schema.service import InstanceSchemaService
from mongo.db import get_mongo_db
from users.models import Users, UserRole
from fastapi import Path
from jsonwebtoken.utils import get_current_user  # Твоя базовая функция авторизации


async def get_current_authorized_schema_user(
    instance_uuid: UUID = Path(...),
    current_user: Users = Depends(get_current_user),
) -> Users:
    """
    Проверяет, имеет ли право пользователь управлять схемой инстанса.
    Доступ разрешен Глобальным Админам ИЛИ Создателю данного конкретного инстанса.
    """
    # Условие 1: Это глобальный админ платформы
    if current_user.role == UserRole.ADMIN:
        return current_user

    # Условие 2: Это создатель инстанса, и UUID инстанса совпадает с его привязкой
    if (
        current_user.role == UserRole.CREATOR
        and current_user.instance_id == instance_uuid
    ):
        return current_user

    # Во всех остальных случаях — от ворот поворот
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to manage this instance schema.",
    )


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
    current_user: Users = Depends(get_current_authorized_schema_user),
    service: InstanceSchemaService = Depends(get_instance_schema_service),
):
    """
    Выгружает ВСЮ конфигурацию инстанса одним JSON.
    Доступно создателю инстанса и системным администраторам.
    """
    # Важно: сервис принимает instance_uuid из пути напрямую, так как админ может выгружать любой инстанс
    return await service.export_schema(instance_uuid)


@router.post("/import", response_model=ImportReport)
async def import_instance_schema(
    instance_uuid: UUID,
    payload: ImportRequest,
    current_user: Users = Depends(get_current_authorized_schema_user),
    service: InstanceSchemaService = Depends(get_instance_schema_service),
):
    """
    Загружает bundle конфигурации.
    Доступно создателю инстанса и системным администраторам.
    """
    report = await service.import_schema(
        instance_uuid=instance_uuid,
        bundle=payload.bundle,
        mode=payload.mode,
        user_uuid=current_user.uuid,
        dry_run=payload.dry_run,
    )

    if not report.valid and not report.created:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=report.model_dump(mode="json"),
        )
    return report

# store/dependecies.py

from mongo.record import RecordRepository
from core.services.template import TemplateService
from core.dependencies import get_template_service
from mongo.db import get_mongo_db
from fastapi import status
from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from database.db import get_db
from users.models import Instances
from store.service import StorefrontService
from policy.models import StorefrontPolicies


async def get_storefront_service(
    pg_session: AsyncSession = Depends(get_db),
    template_service: TemplateService = Depends(get_template_service),
    mongo_db=Depends(get_mongo_db),  # 🔥 Добавляем получение коннекта к MongoDB
) -> StorefrontService:
    """
    Фабрика-провайдер для создания экземпляра StorefrontService.
    FastAPI автоматически разрешит сессию Postgres, коннект к Mongo и TemplateService,
    после чего создаст и вернет сервис витрины.
    """
    # 🔥 Передаем mongo_db в конструктор репозитория, чтобы не было TypeError
    record_repo = RecordRepository(mongo_db)

    return StorefrontService(
        template_service=template_service,
        record_repo=record_repo,
        pg_session=pg_session,
    )


async def get_active_instance_uuid(
    instance_title: str, db: AsyncSession = Depends(get_db)
) -> UUID:
    """
    Зависимость (Dependency) для автоматического резолва
    ЧПУ имени магазина в UUID инстанса.
    """
    stmt = select(Instances).where(
        Instances.title == instance_title,
        Instances.active.is_(
            True
        ),  # 🔥 Flake8 будет молчать, а SQL сгенерируется идеально
    )
    result = await db.execute(stmt)
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    return instance.uuid


async def get_active_policy(
    template_name: str,
    instance_uuid: UUID = Depends(get_active_instance_uuid),
    db: AsyncSession = Depends(get_db),
) -> StorefrontPolicies:
    """
    Гард-зависимость: Проверяет существование политики для публичной витрины.
    Если политики нет — для внешнего мира эндпоинт возвращает 404.
    """
    stmt = select(StorefrontPolicies).where(
        StorefrontPolicies.instance_uuid == instance_uuid,
        StorefrontPolicies.template_name == template_name,
    )
    policy = (await db.execute(stmt)).scalar_one_or_none()
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ресурс не найден или не сконфигурирован для публичного доступа.",
        )
    return policy

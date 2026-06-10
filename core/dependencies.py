# core/dependencies.py

from uuid import UUID
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from users.models import Users, Instances, UserRole
from jsonwebtoken.utils import get_current_user

# Репозитории и Сервисы
from mongo.history import HistoryRepository
from core.services.history import HistoryService
from mongo.template import TemplateRepository
from core.services.template import TemplateService
from mongo.db import get_mongo_db
from motor.motor_asyncio import AsyncIOMotorDatabase
from mongo.record import RecordRepository
from core.services.record import RecordService
from core.services.schema_migration import SchemaMigrationService
from redisdb.cache import CacheLayer
from redisdb.utils import get_redis_db
import config as cfg

# Профессиональные исключения безопасности контуров (Multi-tenancy)
from core.exceptions.dependecies import (
    UserInactiveError,
    CreatorRoleRequiredError,
    InstanceNotFoundError,
    InstanceDeactivatedError,
    InstanceAccessDeniedError,
)


async def get_current_instance_creator(
    instance_uuid: UUID,
    current_user: Users = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Instances:
    """
    Проверяет, что:
    1. Пользователь существует и активен.
    2. Инстанс существует и активен.
    3. Пользователь имеет роль CREATOR и привязан именно к этому инстансу.
    """
    # 1. Явная проверка активности пользователя
    if not current_user.active:
        raise UserInactiveError(user_uuid=current_user.uuid)

    # 2. Проверка роли — управлять структурой инстанса (шаблонами) может только Creator
    if current_user.role != UserRole.CREATOR:
        raise CreatorRoleRequiredError(
            user_uuid=current_user.uuid,
            current_role=current_user.role.value if current_user.role else "NONE",
        )

    # 3. Ищем инстанс в PostgreSQL
    stmt = select(Instances).where(Instances.uuid == instance_uuid)
    result = await session.execute(stmt)
    instance = result.scalar_one_or_none()

    if not instance:
        raise InstanceNotFoundError(instance_uuid=instance_uuid)

    if not instance.active:
        raise InstanceDeactivatedError(instance_uuid=instance_uuid)

    # 4. Проверяем Multi-tenancy связь: принадлежит ли инстанс этому пользователю
    if current_user.instance_id != instance_uuid:
        raise InstanceAccessDeniedError(
            user_uuid=current_user.uuid,
            user_instance_id=current_user.instance_id,
            target_instance_uuid=instance_uuid,
        )

    return instance


async def get_history_service(mongo_db=Depends(get_mongo_db)) -> HistoryService:
    """Инжектирует базу данных в репозиторий, а репозиторий в сервис."""
    repository = HistoryRepository(mongo_db)
    return HistoryService(repository)


async def get_record_service(
    db: AsyncSession = Depends(get_db),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> RecordService:
    record_repo = RecordRepository(mongo_db)
    template_repo = TemplateRepository(mongo_db)

    return RecordService(
        record_repo=record_repo,
        template_repo=template_repo,
        pg_session=db,
        mongo_db=mongo_db,
    )


def get_template_cache(
    redis_client=Depends(get_redis_db("TEMPLATE_CACHE_DB")),
) -> CacheLayer:
    return CacheLayer(redis_client, cfg.TEMPLATE_CACHE_TTL, enabled=cfg.CACHE_ENABLED)


def get_trigger_cache(
    redis_client=Depends(get_redis_db("TRIGGERS_CACHE_DB")),
) -> CacheLayer:
    return CacheLayer(redis_client, cfg.TRIGGERS_CACHE_TTL, enabled=cfg.CACHE_ENABLED)


def get_analytics_cache(
    redis_client=Depends(get_redis_db("ANALYTICS_CACHE_DB")),
) -> CacheLayer:
    return CacheLayer(redis_client, cfg.ANALYTICS_CACHE_TTL, enabled=cfg.CACHE_ENABLED)


async def get_template_service(
    mongo_db=Depends(get_mongo_db),
    cache: CacheLayer = Depends(get_template_cache),
) -> TemplateService:
    repository = TemplateRepository(mongo_db)
    record_repo = RecordRepository(mongo_db)
    schema_migration = SchemaMigrationService(record_repo)
    return TemplateService(
        repository,
        schema_migration=schema_migration,
        cache=cache,
    )


async def get_current_instance_user(
    current_user: Users = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Users:
    """
    Проверяет, что любой пользователь (USER, CREATOR, ADMIN) активен
    и привязан к существующему, активному инстансу.
    Используется для рутинных операций (чтение уведомлений, работа с записями).
    """
    if not current_user.active:
        raise UserInactiveError(user_uuid=current_user.uuid)

    if not current_user.instance_id:
        # Можете использовать вашу кастомную ошибку
        raise InstanceAccessDeniedError(
            user_uuid=current_user.uuid,
            user_instance_id=None,
            target_instance_uuid=None,
        )

    stmt = select(Instances).where(Instances.uuid == current_user.instance_id)
    result = await session.execute(stmt)
    instance = result.scalar_one_or_none()

    if not instance:
        raise InstanceNotFoundError(instance_uuid=current_user.instance_id)

    if not instance.active:
        raise InstanceDeactivatedError(instance_uuid=current_user.instance_id)

    return current_user

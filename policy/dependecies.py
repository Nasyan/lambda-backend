# policy/dependecies.py

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from database.db import get_db
from policy.service import PolicyAdminService
from core.dependencies import get_template_service
from users.models import Users
from jsonwebtoken.utils import get_current_creator

# Импортируем наше профессиональное исключение безопасности
from policy.exceptions.dependencies import CrossTenantAccessDeniedError


def get_policy_admin_service(
    instance_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    template_service=Depends(get_template_service),
) -> PolicyAdminService:
    """Провайдер зависимости для сборки сервиса управления политиками."""
    return PolicyAdminService(template_service=template_service, db_session=db)


def verify_creator_instance_access(
    instance_uuid: UUID, current_creator: Users = Depends(get_current_creator)
) -> Users:
    """
    Внутренний гард роутера: гарантирует, что Креатор имеет право
    изменять конфигурации ТОЛЬКО своего инстанса. Защита от Cross-Tenant атак.
    """
    if current_creator.instance_id != instance_uuid:
        raise CrossTenantAccessDeniedError(
            user_uuid=current_creator.id,
            user_instance_uuid=current_creator.instance_id,
            requested_instance_uuid=instance_uuid,
        )
    return current_creator

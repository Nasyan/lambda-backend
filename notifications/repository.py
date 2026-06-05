# notifications/repository.py

"""Глупый PG-репозиторий шаблонов уведомлений и инбокса (task3, ГЗ-1 Этап 2).

Вынесен из NotificationTemplateService: сервис-оркестратор больше не
содержит SQL. Только select/insert/update/delete."""

from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from middleware.schemas import ListParameters
from notifications.models import NotificationTemplate, NotificationInbox


class NotificationTemplateRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def add(self, template: NotificationTemplate) -> None:
        self.db.add(template)

    async def list(
        self, instance_uuid: UUID, params: Optional[ListParameters] = None
    ) -> List[NotificationTemplate]:
        stmt = select(NotificationTemplate).where(
            NotificationTemplate.instance_uuid == instance_uuid
        )

        if params and params.search:
            stmt = stmt.where(NotificationTemplate.name.ilike(f"%{params.search}%"))

        if params:
            sort_criterion = params.get_postgres_sort(
                model=NotificationTemplate, default_field="created_at"
            )
            stmt = stmt.order_by(sort_criterion)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_uuid(
        self, instance_uuid: UUID, template_uuid: UUID
    ) -> Optional[NotificationTemplate]:
        stmt = select(NotificationTemplate).where(
            NotificationTemplate.uuid == template_uuid,
            NotificationTemplate.instance_uuid == instance_uuid,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update_values(
        self,
        instance_uuid: UUID,
        template_uuid: UUID,
        values: Dict[str, Any],
    ) -> Optional[UUID]:
        stmt = (
            update(NotificationTemplate)
            .where(
                NotificationTemplate.uuid == template_uuid,
                NotificationTemplate.instance_uuid == instance_uuid,
            )
            .values(**values)
            .returning(NotificationTemplate.uuid)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_by_uuid(
        self, instance_uuid: UUID, template_uuid: UUID
    ) -> int:
        stmt = delete(NotificationTemplate).where(
            NotificationTemplate.uuid == template_uuid,
            NotificationTemplate.instance_uuid == instance_uuid,
        )
        result = await self.db.execute(stmt)
        return result.rowcount


class NotificationInboxRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_for_user(self, user_uuid: UUID) -> List[NotificationInbox]:
        stmt = (
            select(NotificationInbox)
            .options(selectinload(NotificationInbox.history))
            .where(NotificationInbox.user_uuid == user_uuid)
            .order_by(NotificationInbox.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def mark_as_read(
        self, user_uuid: UUID, notification_uuid: UUID
    ) -> Optional[UUID]:
        stmt = (
            update(NotificationInbox)
            .where(
                NotificationInbox.uuid == notification_uuid,
                NotificationInbox.user_uuid == user_uuid,
            )
            .values(is_read=True)
            .returning(NotificationInbox.uuid)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

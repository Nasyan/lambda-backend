# triggers/repository.py

"""Глупый PG-репозиторий триггеров (task3, ГЗ-1 Этап 2).

Вынесен из triggers/views.py, где CRUD-запросы жили прямо в роутере.
Только I/O: select/add/delete. Бизнес-оркестрация (валидация, синхронизация
embedded-метаданных, транзакции) — triggers/admin_service.py.
"""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.schemas import ListParameters
from triggers.models import Trigger


class TriggerRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, instance_uuid: UUID, trigger_uuid: UUID) -> Optional[Trigger]:
        result = await self.db.execute(
            select(Trigger).where(
                Trigger.instance_uuid == instance_uuid,
                Trigger.id == trigger_uuid,
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self, instance_uuid: UUID, params: Optional[ListParameters] = None
    ) -> List[Trigger]:
        stmt = select(Trigger).where(Trigger.instance_uuid == instance_uuid)

        if params and params.search:
            stmt = stmt.where(Trigger.name.ilike(f"%{params.search}%"))

        if params:
            sort_criterion = params.get_postgres_sort(
                model=Trigger, default_field="created_at"
            )
            stmt = stmt.order_by(sort_criterion)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    def add(self, trigger: Trigger) -> None:
        self.db.add(trigger)

    async def delete(self, trigger: Trigger) -> None:
        await self.db.delete(trigger)

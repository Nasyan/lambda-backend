# analytics/repository.py

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.models import AnalyticsWidget


class AnalyticsWidgetRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(
        self, instance_uuid: UUID, widget_uuid: UUID
    ) -> Optional[AnalyticsWidget]:
        result = await self.db.execute(
            select(AnalyticsWidget).where(
                AnalyticsWidget.id == widget_uuid,
                AnalyticsWidget.instance_uuid
                == instance_uuid,  # строгая изоляция тенанта
            )
        )
        return result.scalar_one_or_none()

    def add(self, widget: AnalyticsWidget) -> None:
        self.db.add(widget)

    async def list(self, instance_uuid: UUID) -> list[AnalyticsWidget]:
        result = await self.db.execute(
            select(AnalyticsWidget).where(
                AnalyticsWidget.instance_uuid == instance_uuid
            )
        )
        return list(result.scalars().all())

    async def delete(self, widget: AnalyticsWidget) -> None:
        await self.db.delete(widget)

# policy/repository.py

"""Глупый PG-репозиторий политик витрины (task3, ГЗ-1 Этап 2).

Вынесен из PolicyAdminService/StorefrontService: сервисы-оркестраторы
больше не содержат SQL. Только select/add/delete."""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from policy.models import StorefrontPolicies


class PolicyRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_template_name(
        self, instance_uuid: UUID, template_name: str
    ) -> Optional[StorefrontPolicies]:
        stmt = select(StorefrontPolicies).where(
            StorefrontPolicies.instance_uuid == instance_uuid,
            StorefrontPolicies.template_name == template_name,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(
        self, instance_uuid: UUID, policy_id: UUID
    ) -> Optional[StorefrontPolicies]:
        stmt = select(StorefrontPolicies).where(
            StorefrontPolicies.id == policy_id,
            StorefrontPolicies.instance_uuid == instance_uuid,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, instance_uuid: UUID) -> List[StorefrontPolicies]:
        stmt = select(StorefrontPolicies).where(
            StorefrontPolicies.instance_uuid == instance_uuid
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    def add(self, policy: StorefrontPolicies) -> None:
        self.db.add(policy)

    async def delete_by_id(self, instance_uuid: UUID, policy_id: UUID) -> int:
        stmt = delete(StorefrontPolicies).where(
            StorefrontPolicies.id == policy_id,
            StorefrontPolicies.instance_uuid == instance_uuid,
        )
        result = await self.db.execute(stmt)
        return result.rowcount

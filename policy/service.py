# policy/service.py

from typing import List, Dict, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from policy.models import StorefrontPolicies
from policy.schemas import PolicyCreate, PolicyUpdate
from core.services.template import TemplateService
from engine.schema_rules import NoCodeSchemaValidator

# Импортируем наши профессиональные доменные ошибки слоя политик
from policy.exceptions.service import (
    PolicyTemplateNotFoundError,
    PolicyAlreadyExistsError,
    PolicyNotFoundError,
)


class PolicyAdminService:
    def __init__(self, template_service: TemplateService, db_session: AsyncSession):
        self.template_service = template_service
        self.db = db_session

    async def _get_template_schema_by_name(
        self, instance_uuid: UUID, template_name: str
    ) -> Dict[str, Any]:
        """Вспомогательный метод для получения схемы Mongo по текстовому имени шаблона."""
        templates = await self.template_service.get_all_templates(
            instance_uuid=instance_uuid
        )
        for tpl in templates:
            name = tpl.name if hasattr(tpl, "name") else tpl.get("name")
            if name == template_name:
                return (
                    tpl.schema_definition
                    if hasattr(tpl, "schema_definition")
                    else tpl.get("schema", {})
                )

        # Заменяем сырой HTTPException(400) на типизированную ошибку отсутствия таблицы
        raise PolicyTemplateNotFoundError(
            instance_uuid=instance_uuid,
            template_name=template_name,
            message=f"Невозможно настроить витрину. Таблица с именем '{template_name}' отсутствует в CRM.",
        )

    async def create_policy(
        self, instance_uuid: UUID, payload: PolicyCreate
    ) -> StorefrontPolicies:
        # 1. Защита связности: Проверяем, существует ли шаблон и валидируем переданные поля
        schema = await self._get_template_schema_by_name(
            instance_uuid, payload.template_name
        )

        # SchemaDependencyError уже является профессиональным исключением,
        # унаследованным от BaseAppException. Пускаем его наверх без перехвата в HTTPException.
        NoCodeSchemaValidator.validate_storefront_policy(
            schema, payload.model_dump()
        )

        # 2. Проверяем, нет ли уже дублирующей политики для этого шаблона
        existing_stmt = select(StorefrontPolicies).where(
            StorefrontPolicies.instance_uuid == instance_uuid,
            StorefrontPolicies.template_name == payload.template_name,
        )
        existing = (await self.db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            raise PolicyAlreadyExistsError(
                instance_uuid=instance_uuid,
                template_name=payload.template_name,
                message=f"Политика для таблицы '{payload.template_name}' уже создана.",
            )

        # 3. Сохраняем в СУБД
        policy = StorefrontPolicies(
            instance_uuid=instance_uuid,
            template_name=payload.template_name,
            read_filters=payload.read_filters,
            read_mask=payload.read_mask,
            write_mask=payload.write_mask,
            defaults=payload.defaults,
        )
        self.db.add(policy)
        await self.db.commit()
        await self.db.refresh(policy)
        return policy

    async def get_policies_list(self, instance_uuid: UUID) -> List[StorefrontPolicies]:
        stmt = select(StorefrontPolicies).where(
            StorefrontPolicies.instance_uuid == instance_uuid
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_policy(
        self, instance_uuid: UUID, policy_id: UUID, payload: PolicyUpdate
    ) -> StorefrontPolicies:
        stmt = select(StorefrontPolicies).where(
            StorefrontPolicies.id == policy_id,
            StorefrontPolicies.instance_uuid == instance_uuid,
        )
        policy = (await self.db.execute(stmt)).scalar_one_or_none()
        if not policy:
            raise PolicyNotFoundError(instance_uuid=instance_uuid, policy_id=policy_id)

        # Если обновляем маски или фильтры — собираем новое состояние для валидатора
        schema = await self._get_template_schema_by_name(
            instance_uuid, policy.template_name
        )

        updated_data = {
            "read_mask": (
                payload.read_mask if payload.read_mask is not None else policy.read_mask
            ),
            "write_mask": (
                payload.write_mask
                if payload.write_mask is not None
                else policy.write_mask
            ),
            "read_filters": (
                payload.read_filters
                if payload.read_filters is not None
                else policy.read_filters
            ),
            "defaults": (
                payload.defaults
                if payload.defaults is not None
                else getattr(policy, "defaults", {})
            ),
        }

        # Валидируем целостность схемы, исключение SchemaDependencyError летит наверх само
        NoCodeSchemaValidator.validate_storefront_policy(schema, updated_data)

        # Применяем изменения
        if payload.read_mask is not None:
            policy.read_mask = payload.read_mask
        if payload.write_mask is not None:
            policy.write_mask = payload.write_mask
        if payload.read_filters is not None:
            policy.read_filters = payload.read_filters
        if payload.defaults is not None:
            policy.defaults = payload.defaults

        await self.db.commit()
        await self.db.refresh(policy)
        return policy

    async def delete_policy(self, instance_uuid: UUID, policy_id: UUID) -> None:
        stmt = delete(StorefrontPolicies).where(
            StorefrontPolicies.id == policy_id,
            StorefrontPolicies.instance_uuid == instance_uuid,
        )
        result = await self.db.execute(stmt)
        if result.rowcount == 0:
            raise PolicyNotFoundError(instance_uuid=instance_uuid, policy_id=policy_id)
        await self.db.commit()

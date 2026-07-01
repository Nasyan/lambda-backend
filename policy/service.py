# policy/service.py

from typing import List, Dict, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from policy.models import StorefrontPolicies
from policy.repository import PolicyRepository
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
        self.repo = PolicyRepository(db_session)

    async def _get_template_schema_by_name(
        self, instance_uuid: UUID, template_name: str
    ) -> Dict[str, Any]:
        """Получает схему Mongo по имени шаблона (точечный запрос, не обход всех таблиц)."""
        template = await self.template_service.find_by_name(
            instance_uuid=instance_uuid, name=template_name
        )
        if template:
            return template.get("schema", {})

        raise PolicyTemplateNotFoundError(
            instance_uuid=instance_uuid,
            template_name=template_name,
            message=f"Невозможно настроить витрину. Таблица с именем '{template_name}' отсутствует в CRM.",
        )

    async def create_policy(
        self, instance_uuid: UUID, payload: PolicyCreate, commit: bool = True
    ) -> StorefrontPolicies:
        # 1. Защита связности: Проверяем, существует ли шаблон и валидируем переданные поля
        schema = await self._get_template_schema_by_name(
            instance_uuid, payload.template_name
        )

        # SchemaDependencyError уже является профессиональным исключением,
        # унаследованным от BaseAppException. Пускаем его наверх без перехвата в HTTPException.
        NoCodeSchemaValidator.validate_storefront_policy(schema, payload.model_dump())

        # 2. Проверяем, нет ли уже дублирующей политики для этого шаблона
        existing = await self.repo.get_by_template_name(
            instance_uuid, payload.template_name
        )
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
        self.repo.add(policy)
        if commit:
            await self.db.commit()
            await self.db.refresh(policy)
        else:
            await self.db.flush()
        return policy

    async def get_policies_list(self, instance_uuid: UUID) -> List[StorefrontPolicies]:
        return await self.repo.list(instance_uuid)

    async def update_policy(
        self, instance_uuid: UUID, policy_id: UUID, payload: PolicyUpdate
    ) -> StorefrontPolicies:
        policy = await self.repo.get_by_id(instance_uuid, policy_id)
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
        deleted_count = await self.repo.delete_by_id(instance_uuid, policy_id)
        if deleted_count == 0:
            raise PolicyNotFoundError(instance_uuid=instance_uuid, policy_id=policy_id)
        await self.db.commit()

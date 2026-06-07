# store/service.py

from typing import Dict, Any, List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.template import TemplateService
from mongo.record import RecordRepository
from policy.models import StorefrontPolicies
from policy.repository import PolicyRepository
from store.exceptions import (
    StorefrontTemplateNotFoundError,
    StorefrontEmptyWritePayloadError,
)


class StorefrontService:
    def __init__(
        self,
        template_service: TemplateService,
        record_repo: RecordRepository,
        pg_session: AsyncSession,
    ):
        self.template_service = template_service
        self.record_repo = record_repo
        self.pg_session = pg_session
        self.policy_repo = PolicyRepository(pg_session)

    async def _get_policy(
        self, instance_uuid: UUID, template_name: str
    ) -> StorefrontPolicies:
        policy = await self.policy_repo.get_by_template_name(
            instance_uuid, template_name
        )
        if not policy:
            # 🔥 Добавлен пустой словарь defaults для fallback'а
            return StorefrontPolicies(
                read_filters={}, read_mask=[], write_mask=[], defaults={}
            )
        return policy

    async def _resolve_template_uuid(
        self, instance_uuid: str, template_name: str
    ) -> Optional[str]:
        """Разрешает текстовое ЧПУ-имя шаблона в его UUID (точечный запрос, не обход всех таблиц)."""
        template = await self.template_service.find_by_name(
            instance_uuid=UUID(instance_uuid), name=template_name
        )
        if not template:
            return None
        tpl_id = template.get("_id") or template.get("id")
        return str(tpl_id) if tpl_id else None

    def _apply_mask(self, data: Dict[str, Any], mask: List[str]) -> Dict[str, Any]:
        """Оставляет в словаре только те ключи, которые разрешены маской."""
        if not mask:
            return {}
        return {k: v for k, v in data.items() if k in mask}

    async def get_template_schema(
        self, instance_uuid: UUID, template_name: str
    ) -> Dict[str, Any]:
        str_instance = str(instance_uuid)
        template_uuid = await self._resolve_template_uuid(str_instance, template_name)
        if not template_uuid:
            raise StorefrontTemplateNotFoundError(
                instance_uuid=instance_uuid, template_name=template_name
            )

        policy = await self._get_policy(instance_uuid, template_name)
        template = await self.template_service.get_template(
            instance_uuid, UUID(template_uuid)
        )

        full_schema = template.get("schema", {})

        # 🔥 ИСКЛЮЧАЕМ поля с defaults из схемы, отдаваемой клиенту (Скрываем поле "source")
        effective_read_mask = [
            field for field in policy.read_mask if field not in policy.defaults
        ]

        filtered_schema = self._apply_mask(full_schema, effective_read_mask)
        return filtered_schema

    async def get_records(
        self,
        instance_uuid: UUID,
        template_name: str,
        query_filters: Dict[str, Any],
        limit: int,
        offset: int,
    ) -> tuple[List[Dict[str, Any]], int]:  # 🔥 Изменили возвращаемый тип на tuple
        str_instance = str(instance_uuid)
        template_uuid = await self._resolve_template_uuid(str_instance, template_name)
        if not template_uuid:
            raise StorefrontTemplateNotFoundError(
                instance_uuid=instance_uuid, template_name=template_name
            )

        policy = await self._get_policy(instance_uuid, template_name)

        # Объединяем фильтры клиента и жесткие фильтры политики.
        combined_filters = {}

        if policy.read_filters:
            for key, value in policy.read_filters.items():
                combined_filters[key] = value

        for key, value in query_filters.items():
            combined_filters[key] = value

        # 🔥 Распаковываем результаты и общее количество
        records_list, total_count = await self.record_repo.get_records(
            instance_uuid=str_instance,
            template_uuid=template_uuid,
            filters=combined_filters,
            limit=limit,
            offset=offset,
        )

        # 🔥 ИСКЛЮЧАЕМ поля с defaults из выдачи записей клиенту
        effective_read_mask = [
            field for field in policy.read_mask if field not in policy.defaults
        ]

        # Итерируемся строго по списку записей
        for record in records_list:
            if "data" in record:
                record["data"] = self._apply_mask(record["data"], effective_read_mask)

        return records_list, total_count  # 🔥 Возвращаем кортеж обратно

    async def prepare_create_payload(
        self, instance_uuid: UUID, template_name: str, raw_data: Dict[str, Any]
    ) -> tuple[str, Dict[str, Any]]:
        str_instance = str(instance_uuid)
        template_uuid = await self._resolve_template_uuid(str_instance, template_name)
        if not template_uuid:
            raise StorefrontTemplateNotFoundError(
                instance_uuid=instance_uuid, template_name=template_name
            )

        policy = await self._get_policy(instance_uuid, template_name)

        # 🔥 ШАГ 1: Запрещаем клиенту перезаписывать поля, у которых есть жесткий дефолт
        effective_write_mask = [
            field for field in policy.write_mask if field not in policy.defaults
        ]

        # ШАГ 2: Очищаем входящие данные
        cleaned_data = self._apply_mask(raw_data, effective_write_mask)

        # 🔥 ШАГ 3: Принудительно вставляем дефолтные значения политики в итоговый payload
        if policy.defaults:
            cleaned_data.update(policy.defaults)

        # Изменили проверку: если нет ни пользовательских данных, ни дефолтов — тогда ошибка
        if not cleaned_data:
            raise StorefrontEmptyWritePayloadError(template_name=template_name)

        return template_uuid, cleaned_data

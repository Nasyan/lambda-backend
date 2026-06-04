# store/service.py

from typing import Dict, Any, List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException

from core.services.template import TemplateService
from mongo.record import RecordRepository
from policy.models import StorefrontPolicies


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

    async def _get_policy(
        self, instance_uuid: UUID, template_name: str
    ) -> StorefrontPolicies:
        query = select(StorefrontPolicies).where(
            StorefrontPolicies.instance_uuid == instance_uuid,
            StorefrontPolicies.template_name == template_name,
        )
        result = await self.pg_session.execute(query)
        policy = result.scalar_one_or_none()
        if not policy:
            # 🔥 Добавлен пустой словарь defaults для fallback'а
            return StorefrontPolicies(
                read_filters={}, read_mask=[], write_mask=[], defaults={}
            )
        return policy

    async def _resolve_template_uuid(
        self, instance_uuid: str, template_name: str
    ) -> Optional[str]:
        """Разрешает текстовое ЧПУ-имя шаблона в его UUID идентификатор."""
        templates = await self.template_service.get_all_templates(
            instance_uuid=UUID(instance_uuid)
        )
        for tpl in templates:
            name = tpl.name if hasattr(tpl, "name") else tpl.get("name")
            tpl_id = tpl.id if hasattr(tpl, "id") else (tpl.get("_id") or tpl.get("id"))

            if name == template_name:
                return str(tpl_id)
        return None

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
            raise HTTPException(status_code=404, detail="Таблица не найдена")

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
            raise HTTPException(status_code=404, detail="Таблица не найдена")

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
            raise HTTPException(status_code=404, detail="Таблица не найдена")

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
            raise HTTPException(
                status_code=400,
                detail="Нет разрешенных полей для записи или значений по умолчанию",
            )

        return template_uuid, cleaned_data

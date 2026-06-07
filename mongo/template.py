# mongo/template.py

from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from mongo.tools.builders import build_template_document

# Импортируем наши новые профессиональные доменные ошибки
from mongo.exceptions.template import TemplateNotFoundError
from mongo.tools.utils import normalize_template, build_update_meta
from middleware.schemas import ListParameters


class TemplateRepository:
    """Глупый I/O-слой шаблонов (task3, ГЗ-1 Блок A).

    Только чтение и запись документов templates. Валидация определения схемы —
    NoCodeSchemaValidator (вызывается из TemplateService), миграция существующих
    записей под новые правила колонки — SchemaMigrationService, синхронизация
    embedded-метаданных триггеров — TriggerMetadataRepository.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["templates"]

    async def get_template(
        self, instance_uuid: str, template_uuid: str
    ) -> Dict[str, Any]:
        """Получает шаблон по его UUID без нормализации (сырой документ)."""
        query = {"instance_uuid": str(instance_uuid), "_id": str(template_uuid)}
        template = await self.collection.find_one(query)
        if not template:
            raise TemplateNotFoundError(
                template_uuid=template_uuid,
                instance_uuid=instance_uuid,
                message=f"Конфигурация таблицы '{template_uuid}' не найдена.",
            )
        return template

    async def create_template(
        self, instance_uuid: str, name: str, schema: Dict[str, Any], user_uuid: str
    ) -> Dict[str, Any]:
        """Глупая вставка шаблона. Схема обязана быть провалидирована сервисом."""
        template_document = build_template_document(
            instance_uuid=instance_uuid,
            name=name,
            schema=schema,
            user_uuid=user_uuid,
        )

        result = await self.collection.insert_one(template_document)
        template_document["_id"] = str(result.inserted_id)

        return normalize_template(template_document)

    async def get_template_by_uuid(
        self, instance_uuid: str, template_uuid: str
    ) -> Dict[str, Any]:

        query = {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        template = await self.collection.find_one(query)
        if not template:
            raise TemplateNotFoundError(
                template_uuid=template_uuid,
                instance_uuid=instance_uuid,
                message=f"Шаблон таблицы '{template_uuid}' не найден для пространства '{instance_uuid}'.",
            )

        return normalize_template(template)

    async def find_by_name(
        self, instance_uuid: str, name: str
    ) -> Optional[Dict[str, Any]]:
        """Ищет шаблон по его имени внутри конкретного инстанса."""
        # Используем регистронезависимый поиск или точное совпадение.
        # Обычно для имен таблиц лучше делать точное совпадение.
        query = {"instance_uuid": str(instance_uuid), "name": name}
        return await self.collection.find_one(query)

    async def get_all_templates(
        self,
        instance_uuid: str,
        params: Optional[ListParameters] = None,  # 🔥 Делаем опциональным
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:

        # 1. Формируем базовый фильтр по инстансу
        query = {"instance_uuid": str(instance_uuid)}

        # Значения сортировки по умолчанию
        sort_field = "created_at"
        sort_direction = -1  # desc

        # 2. Если параметры переданы (из API роутера), применяем их
        if params:
            if params.search:
                # Регистронезависимый поиск по имени
                query["name"] = {"$regex": params.search, "$options": "i"}

            # Парсим кастомную сортировку
            sort_field, sort_direction = params.get_mongo_sort(
                default_field="created_at"
            )

        # 3. Собираем и выполняем запрос к MongoDB
        cursor = (
            self.collection.find(query)
            .sort(sort_field, sort_direction)
            .skip(offset)
            .limit(limit)
        )

        docs = await cursor.to_list(length=limit)
        return [normalize_template(d) for d in docs]

    async def update_template_metadata(
        self,
        instance_uuid: str,
        template_uuid: str,
        name: str,
        user_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:

        query = {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}

        set_data = build_update_meta(user_uuid)
        set_data["name"] = name.strip()

        updated = await self.collection.find_one_and_update(
            query,
            {"$set": set_data},
            return_document=ReturnDocument.AFTER,
        )

        if not updated:
            raise TemplateNotFoundError(
                template_uuid=template_uuid, instance_uuid=instance_uuid
            )

        return normalize_template(updated)

    async def add_column(
        self,
        instance_uuid: str,
        template_uuid: str,
        column_name: str,
        field_meta: Dict[str, Any],
        user_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Глупый $set новой колонки. Метаданные обязаны быть провалидированы сервисом."""
        query = {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}

        set_data = build_update_meta(user_uuid)
        set_data[f"schema.{column_name}"] = field_meta

        updated = await self.collection.find_one_and_update(
            query,
            {"$set": set_data},
            return_document=ReturnDocument.AFTER,
        )

        if not updated:
            raise TemplateNotFoundError(
                template_uuid=template_uuid, instance_uuid=instance_uuid
            )

        return normalize_template(updated)

    async def drop_column(
        self,
        instance_uuid: str,
        template_uuid: str,
        column_name: str,
        user_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:

        query = {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        set_data = build_update_meta(user_uuid)

        updated = await self.collection.find_one_and_update(
            query,
            {
                "$unset": {f"schema.{column_name}": ""},
                "$set": set_data,
            },
            return_document=ReturnDocument.AFTER,
        )

        if not updated:
            raise TemplateNotFoundError(
                template_uuid=template_uuid, instance_uuid=instance_uuid
            )

        return normalize_template(updated)

    async def delete_template(self, instance_uuid: str, template_uuid: str) -> None:

        query = {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        result = await self.collection.delete_one(query)

        if result.deleted_count == 0:
            raise TemplateNotFoundError(
                template_uuid=template_uuid, instance_uuid=instance_uuid
            )

    async def update_column_meta(
        self,
        instance_uuid: str,
        template_uuid: str,
        column_name: str,
        new_meta: Dict[str, Any],
        user_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Глупый $set метаданных колонки.

        Валидация метаданных, проверка существования колонки и миграция
        существующих записей под новые правила — ответственность
        TemplateService / SchemaMigrationService.
        """
        query = {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}

        set_data = build_update_meta(user_uuid)
        set_data[f"schema.{column_name}"] = new_meta

        updated = await self.collection.find_one_and_update(
            query,
            {"$set": set_data},
            return_document=ReturnDocument.AFTER,
        )

        if not updated:
            raise TemplateNotFoundError(
                template_uuid=template_uuid, instance_uuid=instance_uuid
            )

        return normalize_template(updated)

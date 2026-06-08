# mongo/template.py

from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from mongo.tools.builders import build_template_document

# Импортируем наши новые профессиональные доменные ошибки
from mongo.exceptions.template import TemplateNotFoundError
from mongo.tools.utils import (
    normalize_template,
    build_update_meta,
    with_active_filter,
    with_deleted_filter,
)
from logs.mongo import (
    execute_logged_mongo_call,
    log_mongo_query,
    start_mongo_timer,
    summarize_mongo_document,
)
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
        self.records_collection = db["records"]
        self.history_collection = db["records_history"]

    async def get_template(
        self, instance_uuid: str, template_uuid: str
    ) -> Dict[str, Any]:
        """Получает шаблон по его UUID без нормализации (сырой документ)."""
        query = with_active_filter(
            {"instance_uuid": str(instance_uuid), "_id": str(template_uuid)}
        )
        template = await execute_logged_mongo_call(
            self.collection,
            "find_one",
            query,
            lambda: self.collection.find_one(query),
            lambda result: 1 if result else 0,
        )
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

        result = await execute_logged_mongo_call(
            self.collection,
            "insert_one",
            summarize_mongo_document(template_document),
            lambda: self.collection.insert_one(template_document),
            lambda _: 1,
        )
        template_document["_id"] = str(result.inserted_id)

        return normalize_template(template_document)

    async def get_template_by_uuid(
        self, instance_uuid: str, template_uuid: str
    ) -> Dict[str, Any]:

        query = with_active_filter(
            {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        )
        template = await execute_logged_mongo_call(
            self.collection,
            "find_one",
            query,
            lambda: self.collection.find_one(query),
            lambda result: 1 if result else 0,
        )
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
        query = with_active_filter({"instance_uuid": str(instance_uuid), "name": name})
        return await execute_logged_mongo_call(
            self.collection,
            "find_one",
            query,
            lambda: self.collection.find_one(query),
            lambda result: 1 if result else 0,
        )

    async def get_all_templates(
        self,
        instance_uuid: str,
        params: Optional[ListParameters] = None,  # 🔥 Делаем опциональным
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:

        # 1. Формируем базовый фильтр по инстансу
        query = with_active_filter({"instance_uuid": str(instance_uuid)})

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

        start_time = start_mongo_timer()
        docs = await cursor.to_list(length=limit)
        log_mongo_query(
            self.collection,
            "find",
            query,
            start_time,
            len(docs),
            extra={
                "limit": limit,
                "offset": offset,
                "sort_field": sort_field,
                "sort_direction": sort_direction,
            },
        )
        return [normalize_template(d) for d in docs]

    async def update_template_metadata(
        self,
        instance_uuid: str,
        template_uuid: str,
        name: str,
        user_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:

        query = with_active_filter(
            {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        )

        set_data = build_update_meta(user_uuid)
        set_data["name"] = name.strip()

        update = {"$set": set_data}
        updated = await execute_logged_mongo_call(
            self.collection,
            "find_one_and_update",
            query,
            lambda: self.collection.find_one_and_update(
                query,
                update,
                return_document=ReturnDocument.AFTER,
            ),
            lambda result: 1 if result else 0,
            update=update,
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
        query = with_active_filter(
            {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        )

        set_data = build_update_meta(user_uuid)
        set_data[f"schema.{column_name}"] = field_meta

        update = {"$set": set_data}
        updated = await execute_logged_mongo_call(
            self.collection,
            "find_one_and_update",
            query,
            lambda: self.collection.find_one_and_update(
                query,
                update,
                return_document=ReturnDocument.AFTER,
            ),
            lambda result: 1 if result else 0,
            update=update,
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

        query = with_active_filter(
            {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        )
        set_data = build_update_meta(user_uuid)

        update = {
            "$unset": {f"schema.{column_name}": ""},
            "$set": set_data,
        }
        updated = await execute_logged_mongo_call(
            self.collection,
            "find_one_and_update",
            query,
            lambda: self.collection.find_one_and_update(
                query,
                update,
                return_document=ReturnDocument.AFTER,
            ),
            lambda result: 1 if result else 0,
            update=update,
        )

        if not updated:
            raise TemplateNotFoundError(
                template_uuid=template_uuid, instance_uuid=instance_uuid
            )

        return normalize_template(updated)

    async def delete_template(self, instance_uuid: str, template_uuid: str) -> None:

        query = with_active_filter(
            {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        )
        update = {"$set": {**build_update_meta(), "is_deleted": True}}
        result = await execute_logged_mongo_call(
            self.collection,
            "update_one",
            query,
            lambda: self.collection.update_one(query, update),
            lambda item: item.modified_count,
            update=update,
        )

        if result.matched_count == 0:
            raise TemplateNotFoundError(
                template_uuid=template_uuid, instance_uuid=instance_uuid
            )

        records_query = {
            "instance_uuid": str(instance_uuid),
            "template_uuid": str(template_uuid),
        }
        start_time = start_mongo_timer()
        record_id_docs = await self.records_collection.find(
            records_query, projection={"_id": 1}
        ).to_list(length=None)
        log_mongo_query(
            self.records_collection,
            "find",
            records_query,
            start_time,
            len(record_id_docs),
            extra={"projection": ["_id"]},
        )
        record_ids = [str(doc["_id"]) for doc in record_id_docs]

        records_update = {"$set": {**build_update_meta(), "is_deleted": True}}
        await execute_logged_mongo_call(
            self.records_collection,
            "update_many",
            records_query,
            lambda: self.records_collection.update_many(records_query, records_update),
            lambda item: item.modified_count,
            update=records_update,
        )

        if record_ids:
            history_query = {
                "instance_uuid": str(instance_uuid),
                "record_uuid": {"$in": record_ids},
            }
            history_update = {"$set": {"is_deleted": True}}
            await execute_logged_mongo_call(
                self.history_collection,
                "update_many",
                history_query,
                lambda: self.history_collection.update_many(
                    history_query, history_update
                ),
                lambda item: item.modified_count,
                update=history_update,
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
        query = with_active_filter(
            {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        )

        set_data = build_update_meta(user_uuid)
        set_data[f"schema.{column_name}"] = new_meta

        update = {"$set": set_data}
        updated = await execute_logged_mongo_call(
            self.collection,
            "find_one_and_update",
            query,
            lambda: self.collection.find_one_and_update(
                query,
                update,
                return_document=ReturnDocument.AFTER,
            ),
            lambda result: 1 if result else 0,
            update=update,
        )

        if not updated:
            raise TemplateNotFoundError(
                template_uuid=template_uuid, instance_uuid=instance_uuid
            )

        return normalize_template(updated)

    async def get_deleted_template_by_uuid(
        self, instance_uuid: str, template_uuid: str
    ) -> Dict[str, Any]:
        query = with_deleted_filter(
            {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        )
        template = await execute_logged_mongo_call(
            self.collection,
            "find_one",
            query,
            lambda: self.collection.find_one(query),
            lambda result: 1 if result else 0,
        )
        if not template:
            raise TemplateNotFoundError(
                template_uuid=template_uuid,
                instance_uuid=instance_uuid,
                message=f"Удаленный шаблон таблицы '{template_uuid}' не найден.",
            )
        return normalize_template(template)

    async def get_deleted_templates(
        self,
        instance_uuid: str,
        params: Optional[ListParameters] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        query = with_deleted_filter({"instance_uuid": str(instance_uuid)})
        sort_field = "created_at"
        sort_direction = -1

        if params:
            if params.search:
                query["name"] = {"$regex": params.search, "$options": "i"}
            sort_field, sort_direction = params.get_mongo_sort(
                default_field="created_at"
            )

        cursor = (
            self.collection.find(query)
            .sort(sort_field, sort_direction)
            .skip(offset)
            .limit(limit)
        )
        start_time = start_mongo_timer()
        docs = await cursor.to_list(length=limit)
        log_mongo_query(
            self.collection,
            "find",
            query,
            start_time,
            len(docs),
            extra={
                "limit": limit,
                "offset": offset,
                "sort_field": sort_field,
                "sort_direction": sort_direction,
            },
        )
        return [normalize_template(d) for d in docs]

    async def restore_template(
        self, instance_uuid: str, template_uuid: str
    ) -> Dict[str, Any]:
        query = with_deleted_filter(
            {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
        )
        update = {"$set": {**build_update_meta(), "is_deleted": False}}
        restored = await execute_logged_mongo_call(
            self.collection,
            "find_one_and_update",
            query,
            lambda: self.collection.find_one_and_update(
                query,
                update,
                return_document=ReturnDocument.AFTER,
            ),
            lambda result: 1 if result else 0,
            update=update,
        )
        if not restored:
            raise TemplateNotFoundError(
                template_uuid=template_uuid,
                instance_uuid=instance_uuid,
                message=f"Удаленный шаблон таблицы '{template_uuid}' не найден.",
            )

        records_query = with_deleted_filter(
            {
                "instance_uuid": str(instance_uuid),
                "template_uuid": str(template_uuid),
            }
        )
        start_time = start_mongo_timer()
        record_id_docs = await self.records_collection.find(
            records_query, projection={"_id": 1}
        ).to_list(length=None)
        log_mongo_query(
            self.records_collection,
            "find",
            records_query,
            start_time,
            len(record_id_docs),
            extra={"projection": ["_id"]},
        )
        record_ids = [str(doc["_id"]) for doc in record_id_docs]

        records_update = {"$set": {**build_update_meta(), "is_deleted": False}}
        await execute_logged_mongo_call(
            self.records_collection,
            "update_many",
            records_query,
            lambda: self.records_collection.update_many(records_query, records_update),
            lambda item: item.modified_count,
            update=records_update,
        )

        if record_ids:
            history_query = with_deleted_filter(
                {
                    "instance_uuid": str(instance_uuid),
                    "record_uuid": {"$in": record_ids},
                }
            )
            history_update = {"$set": {"is_deleted": False}}
            await execute_logged_mongo_call(
                self.history_collection,
                "update_many",
                history_query,
                lambda: self.history_collection.update_many(
                    history_query, history_update
                ),
                lambda item: item.modified_count,
                update=history_update,
            )

        return normalize_template(restored)

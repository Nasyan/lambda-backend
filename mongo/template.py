# mongo/template.py

from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from mongo.tools.validators import validate_schema_definition
from mongo.tools.builders import build_template_document

# Импортируем наши новые профессиональные доменные ошибки
from mongo.exceptions.template import (
    TemplateNotFoundError,
    SchemaMutationError,
    TemplateValidationError,
)
from mongo.record import RecordRepository
from mongo.tools.utils import normalize_template, build_update_meta
from middleware.schemas import ListParameters


class TemplateRepository:
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

        try:
            validate_schema_definition(schema)
        except Exception as e:
            raise TemplateValidationError(
                message=f"Ошибка в определении схемы: {str(e)}"
            )

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

        try:
            validate_schema_definition({column_name: field_meta})
        except Exception as e:
            raise TemplateValidationError(
                message=f"Невалидная конфигурация для нового столбца '{column_name}': {str(e)}"
            )

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

        try:
            validate_schema_definition({column_name: new_meta})
        except Exception as e:
            raise TemplateValidationError(
                message=f"Некорректные метаданные для столбца '{column_name}': {str(e)}"
            )

        current_template = await self.get_template_by_uuid(instance_uuid, template_uuid)
        current_schema = current_template.get("schema", {})

        if column_name not in current_schema:
            raise TemplateNotFoundError(
                template_uuid=template_uuid,
                instance_uuid=instance_uuid,
                message=f"Столбец '{column_name}' не существует в схеме таблицы. Изменение метаданных невозможно.",
            )

        record_repo = RecordRepository(self.collection.database)
        try:
            await record_repo.validate_existing_records_against_field(
                instance_uuid=instance_uuid,
                template_uuid=template_uuid,
                column_name=column_name,
                new_field_meta=new_meta,
            )
        except Exception as e:
            # Ловим доменную ошибку валидации записей и переупаковываем её в контекст мутации схемы
            raise SchemaMutationError(
                template_uuid=template_uuid,
                column_name=column_name,
                message=f"Запрещено изменять конфигурацию столбца '{column_name}': существующие данные не соответствуют новым правилам. Детали: {str(e)}",
            )

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

    # --- МЕТОДЫ ДЛЯ СИНХРОНИЗАЦИИ ТРИГГЕРОВ ИЗ POSTGRES ---

    async def inject_trigger_to_schema(
        self,
        instance_uuid: str,
        template_uuid: str,
        column_name: str,
        trigger_data: Dict[str, Any],
        user_uuid: Optional[str] = None,
    ) -> None:
        """Атомарно внедряет или обновляет метаданные триггера внутри конкретной колонки шаблона."""
        query = {
            "_id": str(template_uuid),
            "instance_uuid": str(instance_uuid),
            f"schema.{column_name}": {"$exists": True},
        }

        # 1. Сначала удаляем старую копию триггера по его trigger_id
        await self.collection.update_one(
            query,
            {
                "$pull": {
                    f"schema.{column_name}.triggers": {
                        "trigger_id": str(trigger_data["trigger_id"])
                    }
                }
            },
        )

        # 2. Пушим свежие метаданные триггера в массив
        set_data = build_update_meta(user_uuid)

        result = await self.collection.update_one(
            query,
            {
                "$push": {f"schema.{column_name}.triggers": trigger_data},
                "$set": set_data,
            },
        )

        if result.matched_count == 0:
            raise TemplateNotFoundError(
                template_uuid=template_uuid,
                instance_uuid=instance_uuid,
                message=f"Не удалось привязать триггер: шаблон '{template_uuid}' или колонка '{column_name}' не найдены.",
            )

    async def remove_trigger_from_schema(
        self,
        instance_uuid: str,
        template_uuid: str,
        column_name: str,
        trigger_id: str,
        user_uuid: Optional[str] = None,
    ) -> None:
        """Атомарно удаляет метаданные триггера из массива triggers конкретной колонки."""
        query = {
            "_id": str(template_uuid),
            "instance_uuid": str(instance_uuid),
            f"schema.{column_name}": {"$exists": True},
        }

        set_data = build_update_meta(user_uuid)

        result = await self.collection.update_one(
            query,
            {
                "$pull": {
                    f"schema.{column_name}.triggers": {"trigger_id": str(trigger_id)}
                },
                "$set": set_data,
            },
        )

        if result.matched_count == 0:
            raise TemplateNotFoundError(
                template_uuid=template_uuid,
                instance_uuid=instance_uuid,
                message=f"Не удалось удалить триггер: шаблон '{template_uuid}' или колонка '{column_name}' не найдены.",
            )

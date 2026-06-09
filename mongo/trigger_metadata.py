# mongo/trigger_metadata.py

"""Репозиторий метаданных триггеров внутри no-code схем (task3, ГЗ-1 Блок A).

Выделен из TemplateRepository, чтобы логика структуры таблиц не смешивалась
с автоматизациями: здесь живёт только синхронизация embedded-метаданных
триггеров (Postgres -> Mongo schema.<column>.triggers).
"""

from typing import Dict, Any, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from mongo.exceptions.template import TemplateNotFoundError
from mongo.tools.utils import build_update_meta, with_active_filter
from logs.mongo import execute_logged_mongo_call


class TriggerMetadataRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["templates"]

    async def inject_trigger_to_schema(
        self,
        instance_uuid: str,
        template_uuid: str,
        column_name: str,
        trigger_data: Dict[str, Any],
        user_uuid: Optional[str] = None,
    ) -> None:
        """Атомарно внедряет или обновляет метаданные триггера внутри конкретной колонки шаблона."""
        query = with_active_filter(
            {
                "_id": str(template_uuid),
                "instance_uuid": str(instance_uuid),
                f"schema.{column_name}": {"$exists": True},
            }
        )

        # 1. Сначала удаляем старую копию триггера по его trigger_id
        pull_update = {
            "$pull": {
                f"schema.{column_name}.triggers": {
                    "trigger_id": str(trigger_data["trigger_id"])
                }
            }
        }
        await execute_logged_mongo_call(
            self.collection,
            "update_one",
            query,
            lambda: self.collection.update_one(query, pull_update),
            lambda result: result.modified_count,
            update=pull_update,
        )

        # 2. Пушим свежие метаданные триггера в массив
        set_data = build_update_meta(user_uuid)

        update = {
            "$push": {f"schema.{column_name}.triggers": trigger_data},
            "$set": set_data,
        }
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
        query = with_active_filter(
            {
                "_id": str(template_uuid),
                "instance_uuid": str(instance_uuid),
                f"schema.{column_name}": {"$exists": True},
            }
        )

        set_data = build_update_meta(user_uuid)

        update = {
            "$pull": {
                f"schema.{column_name}.triggers": {"trigger_id": str(trigger_id)}
            },
            "$set": set_data,
        }
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
                template_uuid=template_uuid,
                instance_uuid=instance_uuid,
                message=f"Не удалось удалить триггер: шаблон '{template_uuid}' или колонка '{column_name}' не найдены.",
            )

# mongo/history.py

from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from mongo.tools.builders import build_history_document

# Импортируем наши утилиты
from mongo.tools.utils import stringify_id, stringify_ids_list, extract_field_history


class HistoryRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["records_history"]

    async def log_change(
        self,
        instance_uuid: str,
        record_uuid: str,
        user_uuid: str,
        version: int,
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Сохраняет снимок состояния записи (snapshot) перед её изменением или удалением.
        Метод append-only: записи истории никогда не перезаписываются.
        """
        history_doc = build_history_document(
            instance_uuid=instance_uuid,
            record_uuid=record_uuid,
            user_uuid=user_uuid,
            version=version,
            snapshot=snapshot,
        )

        await self.collection.insert_one(history_doc)
        return stringify_id(history_doc)

    async def get_record_history(
        self, instance_uuid: str, record_uuid: str, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Возвращает полную историю изменений конкретной записи,
        отсортированную от самых свежих версий к самым старым (DESC).
        """
        query = {"instance_uuid": str(instance_uuid), "record_uuid": str(record_uuid)}

        cursor = (
            self.collection.find(query).sort("version", -1).skip(offset).limit(limit)
        )

        results = await cursor.to_list(length=limit)
        return stringify_ids_list(results)

    async def get_snapshot_by_version(
        self, instance_uuid: str, record_uuid: str, version: int
    ) -> Optional[Dict[str, Any]]:
        """Возвращает конкретный снимок записи для реализации фичи отката (Rollback)."""
        query = {
            "instance_uuid": str(instance_uuid),
            "record_uuid": str(record_uuid),
            "version": int(version),
        }
        record = await self.collection.find_one(query)
        return stringify_id(record)

    async def get_field_history(
        self,
        instance_uuid: str,
        record_uuid: str,
        field_name: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Возвращает историю изменений конкретного поля внутри документа.
        Формирует плоский список с указанием версии, значения поля, автора и даты.
        """
        history_records = await self.get_record_history(
            instance_uuid=instance_uuid,
            record_uuid=record_uuid,
            limit=limit,
            offset=offset,
        )

        # Вызываем чистую функцию трансформации из утилит
        return extract_field_history(history_records, field_name)

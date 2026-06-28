# mongo/record.py

from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Any, List, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from mongo.tools.validators import validate_field_name
from mongo.tools.builders import (
    build_record_document,
    build_record_query,
    build_record_update_query,
    build_records_search_query,
    build_records_sort_spec,
)
from mongo.exceptions.record import RecordNotFoundError

from mongo.tools.utils import (
    stringify_id,
    stringify_ids_list,
    validate_dict_keys,
    with_active_filter,
    with_deleted_filter,
)
from logs.mongo import (
    execute_logged_mongo_call,
    log_mongo_query,
    start_mongo_timer,
    summarize_mongo_document,
)


class RecordRepository:
    """Глупый I/O-слой записей (task3, ГЗ-1 Блок B).

    Только insert/update/find/delete. Бизнес-валидация данных по схеме —
    core/validators/record.py (RecordDataValidator), уникальность —
    RecordUniqueConstraintChecker, миграции данных под новую схему —
    core/services/schema_migration.py. Оркестрация — RecordService.

    validate_dict_keys / validate_field_name здесь НЕ бизнес-валидация,
    а защита I/O-слоя от NoSQL-инъекций ($-операторы и точки в ключах) —
    последний рубеж перед базой.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["records"]
        self.history_collection = db["records_history"]

    async def aggregate_records(
        self,
        instance_uuid: str,
        target_template_uuid: str,
        filter_field: str,
        filter_value: Any,
        agg_function: str,
        agg_field: Optional[str] = None,
    ) -> Any:
        """Делает запрос к базе для подсчета суммы, количества, среднего и т.д."""
        match_query = {
            "instance_uuid": str(instance_uuid),
            "template_uuid": str(target_template_uuid),
            f"data.{filter_field}": filter_value,
        }
        match_query = with_active_filter(match_query)

        if agg_function == "count":
            return await execute_logged_mongo_call(
                self.collection,
                "count_documents",
                match_query,
                lambda: self.collection.count_documents(match_query),
                lambda result: result,
            )

        if not agg_field:
            return 0

        pipeline = [
            {"$match": match_query},
            {
                "$group": {
                    "_id": None,
                    "result": {f"${agg_function}": f"$data.{agg_field}"},
                }
            },
        ]

        start_time = start_mongo_timer()
        cursor = self.collection.aggregate(pipeline)
        docs = await cursor.to_list(length=1)
        log_mongo_query(
            self.collection,
            "aggregate",
            pipeline,
            start_time,
            len(docs),
        )

        if docs and docs[0].get("result") is not None:
            return docs[0]["result"]

        return 0

    async def stream_records(
        self, instance_uuid: str, template_uuid: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Потоковый (ленивый) выгрузчик всех записей таблицы без пагинации.
        Используется для триггеров, автоматизаций и тяжелых вычислений.
        """
        query = {
            "instance_uuid": str(instance_uuid),
            "template_uuid": str(template_uuid),
        }
        query = with_active_filter(query)

        # Открываем курсор MongoDB
        start_time = start_mongo_timer()
        cursor = self.collection.find(query)
        documents_returned = 0

        # Лениво отдаем записи по одной по мере их поступления из сети
        try:
            async for record in cursor:
                documents_returned += 1
                if "_id" in record:
                    record["_id"] = str(record["_id"])
                yield record
        finally:
            log_mongo_query(
                self.collection,
                "find",
                query,
                start_time,
                documents_returned,
                extra={"stream": True},
            )

    async def has_field_value_duplicate(
        self,
        instance_uuid: str,
        template_uuid: str,
        field_name: str,
        value: Any,
        exclude_record_uuid: Optional[str] = None,
    ) -> bool:
        """Глупая I/O-проверка: существует ли запись с таким значением поля.

        Решение о нарушении бизнес-уникальности принимает
        RecordUniqueConstraintChecker, репозиторий лишь читает базу.
        """
        query = {
            "instance_uuid": instance_uuid,
            "template_uuid": template_uuid,
            f"data.{field_name}": value,
        }
        query = with_active_filter(query)

        if exclude_record_uuid:
            query["_id"] = {"$ne": exclude_record_uuid}

        duplicate = await execute_logged_mongo_call(
            self.collection,
            "find_one",
            query,
            lambda: self.collection.find_one(query, projection={"_id": 1}),
            lambda result: 1 if result else 0,
            extra={"projection": ["_id"]},
        )
        return duplicate is not None

    async def create_record(
        self,
        instance_uuid: str,
        template_uuid: str,
        data: Dict[str, Any],
        user_uuid: str,
    ) -> Dict[str, Any]:
        """Глупая вставка документа. Данные обязаны быть провалидированы сервисом."""
        validate_dict_keys(data)

        record_document = build_record_document(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            data=data,
            user_uuid=user_uuid,
        )

        await execute_logged_mongo_call(
            self.collection,
            "insert_one",
            summarize_mongo_document(record_document),
            lambda: self.collection.insert_one(record_document),
            lambda _: 1,
        )
        return stringify_id(record_document)

    async def update_record_data(
        self,
        instance_uuid: str,
        record_uuid: str,
        new_data: Dict[str, Any],
        user_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Глупое обновление полей data. Данные обязаны быть провалидированы сервисом."""
        validate_dict_keys(new_data)

        query = with_active_filter(build_record_query(instance_uuid, record_uuid))
        update_query = build_record_update_query(new_data)

        if user_uuid:
            update_query["$set"]["updated_by"] = str(user_uuid)

        updated_record = await execute_logged_mongo_call(
            self.collection,
            "find_one_and_update",
            query,
            lambda: self.collection.find_one_and_update(
                query,
                update_query,
                return_document=ReturnDocument.AFTER,
            ),
            lambda result: 1 if result else 0,
            update=update_query,
        )

        if not updated_record:
            raise RecordNotFoundError(
                record_uuid=record_uuid,
                instance_uuid=instance_uuid,
                message=f"Запись '{record_uuid}' не найдена в инстансе '{instance_uuid}'. Обновление отклонено.",
            )

        return stringify_id(updated_record)

    async def set_record_data_field(
        self,
        record_id: Any,
        column_name: str,
        value: Any,
    ) -> None:
        """Точечный $set одного поля data по первичному ключу (для миграций схемы)."""
        query = with_active_filter({"_id": record_id})
        update = {"$set": {f"data.{column_name}": value}}
        await execute_logged_mongo_call(
            self.collection,
            "update_one",
            query,
            lambda: self.collection.update_one(query, update),
            lambda result: result.modified_count,
            update=update,
        )

    async def get_record_by_uuid(
        self,
        instance_uuid: str,
        record_uuid: str,
    ) -> Dict[str, Any]:

        query = with_active_filter(build_record_query(instance_uuid, record_uuid))
        record = await execute_logged_mongo_call(
            self.collection,
            "find_one",
            query,
            lambda: self.collection.find_one(query),
            lambda result: 1 if result else 0,
        )

        if not record:
            raise RecordNotFoundError(
                record_uuid=record_uuid,
                instance_uuid=instance_uuid,
                message=f"Документ с UUID '{record_uuid}' не существует в пространстве '{instance_uuid}'.",
            )

        return stringify_id(record)

    async def get_records(
        self,
        instance_uuid: str,
        template_uuid: str,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: Optional[str] = None,
        sort_descending: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:  # <-- Возвращаем (список, count)

        filters = filters or {}
        validate_dict_keys(filters)

        # Строим query один раз — он пойдет и в count, и в find
        query = with_active_filter(
            build_records_search_query(instance_uuid, template_uuid, filters)
        )

        # 1. Параллельно или последовательно считаем общее количество документов в БД по этой выборке
        total_count = await execute_logged_mongo_call(
            self.collection,
            "count_documents",
            query,
            lambda: self.collection.count_documents(query),
            lambda result: result,
        )

        if sort_by:
            validate_field_name(sort_by)

        sort_spec = build_records_sort_spec(sort_by, sort_descending)

        # 2. Получаем саму страницу данных с skip (offset) и limit
        cursor = self.collection.find(query).skip(offset).limit(limit)

        if sort_spec:
            cursor = cursor.sort(sort_spec)

        start_time = start_mongo_timer()
        results = await cursor.to_list(length=limit)
        log_mongo_query(
            self.collection,
            "find",
            query,
            start_time,
            len(results),
            extra={"limit": limit, "offset": offset, "sort": sort_spec},
        )

        return stringify_ids_list(results), total_count

    async def delete_record(
        self,
        instance_uuid: str,
        record_uuid: str,
        template_uuid: Optional[str] = None,
        user_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:

        query = with_active_filter(build_record_query(instance_uuid, record_uuid))
        if template_uuid is not None:
            query["template_uuid"] = str(template_uuid)
        # Удаление — тоже изменение: версия инкрементируется, updated_* ставятся
        # (задание 3 — консистентный аудит-трейл).
        update: Dict[str, Any] = {
            "$set": {
                "is_deleted": True,
                "updated_at": datetime.now(timezone.utc),
            },
            "$inc": {"version": 1},
        }
        if user_uuid:
            update["$set"]["updated_by"] = str(user_uuid)
        deleted_record = await execute_logged_mongo_call(
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

        if not deleted_record:
            raise RecordNotFoundError(
                record_uuid=record_uuid,
                instance_uuid=instance_uuid,
                message=f"Запись '{record_uuid}' не найдена для удаления.",
            )

        history_query = {
            "instance_uuid": str(instance_uuid),
            "record_uuid": str(record_uuid),
        }
        history_update = {"$set": {"is_deleted": True}}
        await execute_logged_mongo_call(
            self.history_collection,
            "update_many",
            history_query,
            lambda: self.history_collection.update_many(history_query, history_update),
            lambda item: item.modified_count,
            update=history_update,
        )

        return stringify_id(deleted_record)

    async def get_records_by_uuids(
        self,
        instance_uuid: str,
        record_uuids: List[str],
        template_uuid: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:

        if not record_uuids:
            return {}

        query = {
            "instance_uuid": str(instance_uuid),
            "_id": {"$in": [str(record_uuid) for record_uuid in record_uuids]},
        }
        query = with_active_filter(query)
        if template_uuid is not None:
            query["template_uuid"] = str(template_uuid)

        cursor = self.collection.find(query)
        result_map = {}
        start_time = start_mongo_timer()

        async for record in cursor:
            stringify_id(record)
            result_map[record["_id"]] = record

        log_mongo_query(
            self.collection,
            "find",
            query,
            start_time,
            len(result_map),
        )

        return result_map

    async def get_record_by_custom_field(
        self,
        instance_uuid: str,
        field_name: str,
        value: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        Ищет запись по динамическому полю.
        Ожидается, что field_name уже содержит префикс (например, 'data.qr_code').
        """
        query = with_active_filter(
            {"instance_uuid": str(instance_uuid), field_name: value}
        )
        record = await execute_logged_mongo_call(
            self.collection,
            "find_one",
            query,
            lambda: self.collection.find_one(query),
            lambda result: 1 if result else 0,
        )
        return stringify_id(record) if record else None

    async def get_deleted_records(
        self,
        instance_uuid: str,
        template_uuid: str,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: Optional[str] = None,
        sort_descending: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        filters = filters or {}
        validate_dict_keys(filters)

        query = with_deleted_filter(
            build_records_search_query(instance_uuid, template_uuid, filters)
        )

        total_count = await execute_logged_mongo_call(
            self.collection,
            "count_documents",
            query,
            lambda: self.collection.count_documents(query),
            lambda result: result,
        )

        if sort_by:
            validate_field_name(sort_by)

        sort_spec = build_records_sort_spec(sort_by, sort_descending)
        cursor = self.collection.find(query).skip(offset).limit(limit)
        if sort_spec:
            cursor = cursor.sort(sort_spec)

        start_time = start_mongo_timer()
        results = await cursor.to_list(length=limit)
        log_mongo_query(
            self.collection,
            "find",
            query,
            start_time,
            len(results),
            extra={"limit": limit, "offset": offset, "sort": sort_spec},
        )

        return stringify_ids_list(results), total_count

    async def restore_record(
        self,
        instance_uuid: str,
        record_uuid: str,
        template_uuid: Optional[str] = None,
        user_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = with_deleted_filter(build_record_query(instance_uuid, record_uuid))
        if template_uuid is not None:
            query["template_uuid"] = str(template_uuid)

        # Восстановление — изменение: версия и updated_* двигаются (задание 3).
        update: Dict[str, Any] = {
            "$set": {
                "is_deleted": False,
                "updated_at": datetime.now(timezone.utc),
            },
            "$inc": {"version": 1},
        }
        if user_uuid:
            update["$set"]["updated_by"] = str(user_uuid)
        restored_record = await execute_logged_mongo_call(
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

        if not restored_record:
            raise RecordNotFoundError(
                record_uuid=record_uuid,
                instance_uuid=instance_uuid,
                message=f"Удаленная запись '{record_uuid}' не найдена для восстановления.",
            )

        history_query = with_deleted_filter(
            {"instance_uuid": str(instance_uuid), "record_uuid": str(record_uuid)}
        )
        history_update = {"$set": {"is_deleted": False}}
        await execute_logged_mongo_call(
            self.history_collection,
            "update_many",
            history_query,
            lambda: self.history_collection.update_many(history_query, history_update),
            lambda item: item.modified_count,
            update=history_update,
        )

        return stringify_id(restored_record)

    async def purge_records_by_template(
        self, instance_uuid: str, template_uuid: str
    ) -> int:
        """
        Каскадное физическое удаление всех записей (и их истории)
        при жестком удалении шаблона (Force Delete).
        """
        query = {
            "instance_uuid": str(instance_uuid),
            "template_uuid": str(template_uuid),
        }

        # 1. Безвозвратно удаляем сами записи
        records_delete_result = await execute_logged_mongo_call(
            self.collection,
            "delete_many",
            query,
            lambda: self.collection.delete_many(query),
            lambda result: result.deleted_count,
        )

        # 2. Безвозвратно удаляем их историю
        await execute_logged_mongo_call(
            self.history_collection,
            "delete_many",
            query,
            lambda: self.history_collection.delete_many(query),
            lambda result: result.deleted_count,
        )

        return records_delete_result

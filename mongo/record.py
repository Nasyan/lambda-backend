# mongo/record.py

from typing import AsyncGenerator, Dict, Any, List, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from mongo.tools.validators import validate_record_data, validate_field_name
from mongo.tools.builders import (
    build_record_document,
    build_record_query,
    build_record_update_query,
    build_records_search_query,
    build_records_sort_spec,
)
from mongo.exceptions.record import (
    RecordNotFoundError,
    RecordValidationError,
    DuplicateRecordKeyError,
)

from mongo.tools.utils import stringify_id, stringify_ids_list, validate_dict_keys


class RecordRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["records"]

    async def check_unique_constraints(
        self,
        instance_uuid: str,
        template_uuid: str,
        data: Dict[str, Any],
        schema: Dict[str, Any],
        exclude_record_uuid: Optional[str] = None,
    ) -> None:
        """
        Проверяет, нет ли в базе записей, у которых совпадают поля,
        помеченные как unique: True в схеме шаблона.
        """
        for field_name, field_meta in schema.items():
            if field_meta.get("unique") is True and field_name in data:
                field_value = data[field_name]

                query = {
                    "instance_uuid": instance_uuid,
                    "template_uuid": template_uuid,
                    f"data.{field_name}": field_value,
                }

                if exclude_record_uuid:
                    query["_id"] = {"$ne": exclude_record_uuid}

                duplicate = await self.collection.find_one(query, projection={"_id": 1})

                if duplicate:
                    # Выбрасываем специализированную ошибку дубликата бизнес-ключа с деталями
                    raise DuplicateRecordKeyError(
                        message=f"Ошибка уникальности: Поле '{field_name}' уже содержит значение '{field_value}'.",
                        field_name=field_name,
                        invalid_value=field_value,
                        reason="unique_constraint_violation",
                    )

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

        if agg_function == "count":
            return await self.collection.count_documents(match_query)

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

        cursor = self.collection.aggregate(pipeline)
        docs = await cursor.to_list(length=1)

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

        # Открываем курсор MongoDB
        cursor = self.collection.find(query)

        # Лениво отдаем записи по одной по мере их поступления из сети
        async for record in cursor:
            if "_id" in record:
                record["_id"] = str(record["_id"])
            yield record

    async def create_record(
        self,
        instance_uuid: str,
        template_uuid: str,
        data: Dict[str, Any],
        schema: Dict[str, Any],
        user_uuid: str,
        s3_service: Optional[Any] = None,
    ) -> Dict[str, Any]:

        validate_dict_keys(data)

        try:
            await validate_record_data(data, schema, s3_service=s3_service)
        except Exception as e:
            raise RecordValidationError(
                message=str(e), reason="schema_validation_error"
            )

        await self.check_unique_constraints(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            data=data,
            schema=schema,
        )

        record_document = build_record_document(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            data=data,
            user_uuid=user_uuid,
        )

        await self.collection.insert_one(record_document)
        return stringify_id(record_document)

    async def update_record_data(
        self,
        instance_uuid: str,
        template_uuid: str,
        record_uuid: str,
        new_data: Dict[str, Any],
        schema: Dict[str, Any],
        user_uuid: Optional[str] = None,
        s3_service: Optional[Any] = None,
    ) -> Dict[str, Any]:

        validate_dict_keys(new_data)

        try:
            await validate_record_data(new_data, schema, s3_service=s3_service)
        except Exception as e:
            raise RecordValidationError(
                message=str(e), reason="schema_validation_error"
            )

        await self.check_unique_constraints(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            data=new_data,
            schema=schema,
            exclude_record_uuid=record_uuid,
        )

        query = build_record_query(instance_uuid, record_uuid)
        update_query = build_record_update_query(new_data)

        if user_uuid:
            update_query["$set"]["updated_by"] = str(user_uuid)

        updated_record = await self.collection.find_one_and_update(
            query,
            update_query,
            return_document=ReturnDocument.AFTER,
        )

        if not updated_record:
            raise RecordNotFoundError(
                record_uuid=record_uuid,
                instance_uuid=instance_uuid,
                message=f"Запись '{record_uuid}' не найдена в инстансе '{instance_uuid}'. Обновление отклонено.",
            )

        return stringify_id(updated_record)

    async def get_record_by_uuid(
        self,
        instance_uuid: str,
        record_uuid: str,
    ) -> Dict[str, Any]:

        query = build_record_query(instance_uuid, record_uuid)
        record = await self.collection.find_one(query)

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
        query = build_records_search_query(instance_uuid, template_uuid, filters)

        # 1. Параллельно или последовательно считаем общее количество документов в БД по этой выборке
        total_count = await self.collection.count_documents(query)

        if sort_by:
            validate_field_name(sort_by)

        sort_spec = build_records_sort_spec(sort_by, sort_descending)

        # 2. Получаем саму страницу данных с skip (offset) и limit
        cursor = self.collection.find(query).skip(offset).limit(limit)

        if sort_spec:
            cursor = cursor.sort(sort_spec)

        results = await cursor.to_list(length=limit)

        return stringify_ids_list(results), total_count

    async def delete_record(
        self,
        instance_uuid: str,
        record_uuid: str,
    ) -> None:

        query = build_record_query(instance_uuid, record_uuid)
        result = await self.collection.delete_one(query)

        if result.deleted_count == 0:
            raise RecordNotFoundError(
                record_uuid=record_uuid,
                instance_uuid=instance_uuid,
                message=f"Запись '{record_uuid}' не найдена для удаления.",
            )

    async def validate_existing_records_against_field(
        self,
        instance_uuid: str,
        template_uuid: str,
        column_name: str,
        new_field_meta: Dict[str, Any],
        s3_service: Optional[Any] = None,
    ) -> None:

        query = {
            "instance_uuid": str(instance_uuid),
            "template_uuid": str(template_uuid),
        }
        target_schema = {column_name: new_field_meta}
        is_required = new_field_meta.get("required", False)

        cursor = self.collection.find(query)

        async for record in cursor:
            record_id = record.get("_id")
            record_data = record.get("data", {})

            if is_required and column_name not in record_data:
                raise RecordValidationError(
                    message=f"Существующая запись с ID '{record_id}' не содержит обязательного поля '{column_name}'.",
                    field_name=column_name,
                    reason="retroactive_required_constraint_failed",
                )

            if column_name in record_data:
                isolated_data = {column_name: record_data[column_name]}

                try:
                    await validate_record_data(
                        isolated_data, target_schema, s3_service=s3_service
                    )
                except Exception as e:
                    raise RecordValidationError(
                        message=f"Ошибка обратной совместимости в записи '{record_id}': {str(e)}",
                        field_name=column_name,
                        reason="retroactive_type_migration_failed",
                    )

                if isolated_data[column_name] != record_data[column_name]:
                    await self.collection.update_one(
                        {"_id": record_id},
                        {"$set": {f"data.{column_name}": isolated_data[column_name]}},
                    )

    async def get_records_by_uuids(
        self,
        instance_uuid: str,
        record_uuids: List[str],
    ) -> Dict[str, Dict[str, Any]]:

        if not record_uuids:
            return {}

        query = {"instance_uuid": str(instance_uuid), "_id": {"$in": record_uuids}}
        cursor = self.collection.find(query)
        result_map = {}

        async for record in cursor:
            stringify_id(record)
            result_map[record["_id"]] = record

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
        query = {"instance_uuid": str(instance_uuid), field_name: value}
        record = await self.collection.find_one(query)
        return stringify_id(record) if record else None

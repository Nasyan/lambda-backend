from collections import defaultdict
import re
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Set, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

from mongo.tools.utils import stringify_id


class BatchDataLoader:
    """
    Per-execution lazy Mongo loader with tenant-scoped cache.

    IDs are collected first and loaded by template with one `$in` query per pending
    group. Subsequent reads come from the in-memory session cache.
    """

    def __init__(self, mongo_db: AsyncIOMotorDatabase, instance_uuid: str):
        self.mongo_db = mongo_db
        self.instance_uuid = str(instance_uuid)
        self.collection = mongo_db["records"]
        self._pending_ids: DefaultDict[Tuple[str, Optional[str]], Set[str]] = (
            defaultdict(set)
        )
        self._records: Dict[Tuple[str, Optional[str], str], Dict[str, Any]] = {}
        self._field_cache: Dict[
            Tuple[str, Optional[str], str, Tuple[str, ...]],
            Dict[Any, Dict[str, Any]],
        ] = {}

    def collect_ids(
        self, template_uuid: Optional[str], record_ids: Iterable[Any]
    ) -> None:
        for record_id in record_ids:
            normalized_id = self._normalize_record_id(record_id)
            if not normalized_id:
                continue
            cache_key = (
                self.instance_uuid,
                self._normalize_template_uuid(template_uuid),
                normalized_id,
            )
            if cache_key not in self._records:
                self._pending_ids[(self.instance_uuid, cache_key[1])].add(
                    normalized_id
                )

    async def load(self) -> None:
        for cache_key, record_ids in list(self._pending_ids.items()):
            if not record_ids:
                continue

            cache_instance_uuid, template_uuid = cache_key
            query: Dict[str, Any] = {
                "instance_uuid": cache_instance_uuid,
                "_id": {"$in": sorted(record_ids)},
            }
            if template_uuid is not None:
                query["template_uuid"] = template_uuid

            cursor = self.collection.find(query)
            async for record in cursor:
                stringify_id(record)
                record_cache_key = (
                    cache_instance_uuid,
                    template_uuid,
                    str(record["_id"]),
                )
                self._records[record_cache_key] = record

            self._pending_ids[cache_key].clear()

    async def prefetch(
        self, template_uuid: Optional[str], record_ids: Iterable[Any]
    ) -> None:
        self.collect_ids(template_uuid, record_ids)
        await self.load()

    async def get_many(
        self, template_uuid: Optional[str], record_ids: Iterable[Any]
    ) -> Dict[str, Dict[str, Any]]:
        normalized_template_uuid = self._normalize_template_uuid(template_uuid)
        normalized_ids = [
            normalized_id
            for normalized_id in (
                self._normalize_record_id(record_id) for record_id in record_ids
            )
            if normalized_id
        ]
        self.collect_ids(normalized_template_uuid, normalized_ids)
        await self.load()
        return {
            record_id: self._records[
                (self.instance_uuid, normalized_template_uuid, record_id)
            ]
            for record_id in normalized_ids
            if (self.instance_uuid, normalized_template_uuid, record_id)
            in self._records
        }

    async def get_one(
        self, template_uuid: Optional[str], record_id: Any
    ) -> Optional[Dict[str, Any]]:
        records = await self.get_many(template_uuid, [record_id])
        normalized_id = self._normalize_record_id(record_id)
        return records.get(normalized_id) if normalized_id else None

    async def get_by_field_many(
        self,
        template_uuid: Optional[str],
        field_name: str,
        values: Iterable[Any],
    ) -> Dict[Any, Dict[str, Any]]:
        normalized_template_uuid = self._normalize_template_uuid(template_uuid)
        normalized_values = tuple(
            sorted({str(value) for value in values if value is not None})
        )
        if not normalized_values:
            return {}

        cache_key = (
            self.instance_uuid,
            normalized_template_uuid,
            field_name,
            normalized_values,
        )
        if cache_key in self._field_cache:
            return self._field_cache[cache_key]

        query: Dict[str, Any] = {
            "instance_uuid": self.instance_uuid,
            field_name: {"$in": list(normalized_values)},
        }
        if normalized_template_uuid is not None:
            query["template_uuid"] = normalized_template_uuid

        result: Dict[Any, Dict[str, Any]] = {}
        cursor = self.collection.find(query)
        async for record in cursor:
            stringify_id(record)
            field_value = self._resolve_dotted(record, field_name)
            if field_value is not None:
                result[str(field_value)] = record
                self._records[
                    (self.instance_uuid, normalized_template_uuid, str(record["_id"]))
                ] = record

        self._field_cache[cache_key] = result
        return result

    async def get_by_field_one(
        self,
        template_uuid: Optional[str],
        field_name: str,
        value: Any,
    ) -> Optional[Dict[str, Any]]:
        records = await self.get_by_field_many(template_uuid, field_name, [value])
        return records.get(str(value))

    async def aggregate_records(
        self,
        target_template_uuid: str,
        filter_field: str,
        filter_value: Any,
        agg_function: str,
        agg_field: Optional[str] = None,
    ) -> Any:
        match_query = {
            "instance_uuid": self.instance_uuid,
            "template_uuid": str(target_template_uuid),
            f"data.{filter_field}": filter_value,
        }

        if agg_function == "count":
            return await self.collection.count_documents(match_query)

        if not agg_field:
            return 0

        cursor = self.collection.aggregate(
            [
                {"$match": match_query},
                {
                    "$group": {
                        "_id": None,
                        "result": {f"${agg_function}": f"$data.{agg_field}"},
                    }
                },
            ]
        )
        docs = await cursor.to_list(length=1)
        if docs and docs[0].get("result") is not None:
            return docs[0]["result"]
        return 0

    async def query_records(
        self,
        target_template_uuid: str,
        filters: List[Dict[str, Any]],
        limit: int = 20,
        return_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {
            "instance_uuid": self.instance_uuid,
            "template_uuid": str(target_template_uuid),
        }
        for item in filters:
            field_name = item["field"]
            operator = item.get("operator", "eq")
            value = item.get("value")
            mongo_field = self._data_field(field_name)
            if operator == "eq":
                query[mongo_field] = value
            elif operator == "ne":
                query[mongo_field] = {"$ne": value}
            elif operator == "gt":
                query[mongo_field] = {"$gt": value}
            elif operator == "lt":
                query[mongo_field] = {"$lt": value}
            elif operator == "gte":
                query[mongo_field] = {"$gte": value}
            elif operator == "lte":
                query[mongo_field] = {"$lte": value}
            elif operator == "contains":
                query[mongo_field] = {
                    "$regex": re.escape(str(value)),
                    "$options": "i",
                }
            else:
                raise ValueError(f"Unsupported query operator '{operator}'")

        projection = None
        if return_fields:
            projection = {f"data.{field_name}": 1 for field_name in return_fields}
            projection.update({"_id": 1, "instance_uuid": 1, "template_uuid": 1})

        cursor = self.collection.find(query, projection=projection).limit(limit)
        records = await cursor.to_list(length=limit)
        return [stringify_id(record) for record in records]

    def _normalize_template_uuid(self, template_uuid: Optional[str]) -> Optional[str]:
        return str(template_uuid) if template_uuid else None

    def _normalize_record_id(self, record_id: Any) -> Optional[str]:
        if not record_id:
            return None
        if isinstance(record_id, dict):
            raw_value = (
                record_id.get("_id")
                or record_id.get("uuid")
                or record_id.get("target_uuid")
            )
            return str(raw_value) if raw_value else None
        return str(record_id)

    def _resolve_dotted(self, document: Dict[str, Any], field_name: str) -> Any:
        current: Any = document
        for part in field_name.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def _data_field(self, field_name: str) -> str:
        if field_name.startswith("data.") or field_name.startswith("_"):
            return field_name
        return f"data.{field_name}"

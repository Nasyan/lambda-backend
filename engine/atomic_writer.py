import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import InsertOne, UpdateOne
from pymongo.errors import BulkWriteError

from mongo.tools.utils import stringify_id


class TargetAtomicWriter:
    """
    Accumulates abstract DML operations and flushes them as one unordered bulk_write.
    """

    def __init__(
        self,
        mongo_db: AsyncIOMotorDatabase,
        instance_uuid: str,
        target_template_uuid: str,
    ):
        self.mongo_db = mongo_db
        self.instance_uuid = str(instance_uuid)
        self.target_template_uuid = str(target_template_uuid)
        self.collection = mongo_db["records"]
        self._operations: List[Any] = []
        # Параллельный _operations список целей: ("id", record_uuid) либо
        # ("filter", filter_doc). После flush() в touched-наборы попадают
        # ТОЛЬКО реально записанные операции (ГЗ-2 п.2): упавшие в
        # BulkWriteError документы не должны порождать каскады.
        self._op_targets: List[tuple] = []
        self._touched_record_ids: Set[str] = set()
        self._touched_filters: List[Dict[str, Any]] = []

    def add_insert(self, data: Dict[str, Any], user_uuid: str = "system") -> str:
        now = datetime.now(timezone.utc)
        record_uuid = str(data.get("_id") or data.get("uuid") or uuid.uuid4())
        document = {
            "_id": record_uuid,
            "instance_uuid": self.instance_uuid,
            "template_uuid": self.target_template_uuid,
            "data": self._strip_system_fields(data),
            "version": 1,
            "created_by": str(user_uuid),
            "created_at": now,
            "updated_at": now,
        }
        self._operations.append(InsertOne(document))
        self._op_targets.append(("id", record_uuid))
        return record_uuid

    def add_update(
        self,
        record_uuid: Optional[str],
        operations: Any,
        upsert: bool = False,
        search_filter: Optional[Dict[str, Any]] = None,
    ) -> None:
        filter_doc = {
            "instance_uuid": self.instance_uuid,
            "template_uuid": self.target_template_uuid,
        }
        op_target: tuple
        if record_uuid:
            filter_doc["_id"] = str(record_uuid)
            op_target = ("id", str(record_uuid))
        elif search_filter:
            filter_doc.update(self._to_data_filter(search_filter))
            op_target = ("filter", dict(filter_doc))
        else:
            raise ValueError("record_uuid or search_filter is required for update")

        update_doc = self._translate_operations(operations)
        if upsert:
            upsert_record_uuid = str(record_uuid or uuid.uuid4())
            update_doc.setdefault("$setOnInsert", {}).update(
                {
                    "_id": upsert_record_uuid,
                    "instance_uuid": self.instance_uuid,
                    "template_uuid": self.target_template_uuid,
                    "created_by": "system_automation",
                    "created_at": datetime.now(timezone.utc),
                }
            )
            if op_target[0] == "filter":
                # upsert по фильтру: успешная операция могла и обновить
                # существующую запись (фильтр), и вставить новую (id) —
                # отслеживаем обе цели.
                op_target = ("filter_or_id", dict(op_target[1]), upsert_record_uuid)
            else:
                op_target = ("id", upsert_record_uuid)

        update_doc.setdefault("$set", {})["updated_at"] = datetime.now(timezone.utc)
        update_doc.setdefault("$inc", {})["version"] = 1
        self._operations.append(UpdateOne(filter_doc, update_doc, upsert=upsert))
        self._op_targets.append(op_target)

    async def flush(self) -> Dict[str, Any]:
        if not self._operations:
            return {
                "matched_count": 0,
                "modified_count": 0,
                "upserted_count": 0,
                "inserted_count": 0,
                "failed_count": 0,
                "write_errors": [],
            }

        failed_indexes: Set[int] = set()
        write_errors: List[Dict[str, Any]] = []

        try:
            result = await self.collection.bulk_write(self._operations, ordered=False)
            counts = {
                "matched_count": result.matched_count,
                "modified_count": result.modified_count,
                "upserted_count": result.upserted_count,
                "inserted_count": result.inserted_count,
            }
        except BulkWriteError as exc:
            # ordered=False: Mongo выполнил ВСЕ операции, часть упала.
            # Частичный сбой не должен ронять пайплайн (ГЗ-2 п.2):
            # фиксируем какие операции не записались, остальные считаем
            # успешными, и только они попадут в touched/каскады.
            details = exc.details or {}
            for write_error in details.get("writeErrors", []):
                index = write_error.get("index")
                if index is not None:
                    failed_indexes.add(int(index))
                write_errors.append(
                    {
                        "index": index,
                        "code": write_error.get("code"),
                        "errmsg": write_error.get("errmsg"),
                    }
                )
            counts = {
                "matched_count": details.get("nMatched", 0),
                "modified_count": details.get("nModified", 0),
                "upserted_count": details.get("nUpserted", 0),
                "inserted_count": details.get("nInserted", 0),
            }

        # Только реально записанные операции попадают в touched-наборы.
        for index, op_target in enumerate(self._op_targets):
            if index in failed_indexes:
                continue
            kind = op_target[0]
            if kind == "id":
                self._touched_record_ids.add(op_target[1])
            elif kind == "filter":
                self._touched_filters.append(op_target[1])
            elif kind == "filter_or_id":
                self._touched_filters.append(op_target[1])
                self._touched_record_ids.add(op_target[2])

        self._operations = []
        self._op_targets = []
        counts["failed_count"] = len(failed_indexes)
        counts["write_errors"] = write_errors
        return counts

    async def fetch_touched_records(self) -> List[Dict[str, Any]]:
        if not self._touched_record_ids and not self._touched_filters:
            return []

        base_query = {
            "instance_uuid": self.instance_uuid,
            "template_uuid": self.target_template_uuid,
        }
        branches = []
        if self._touched_record_ids:
            branches.append({"_id": {"$in": sorted(self._touched_record_ids)}})
        branches.extend(self._touched_filters)
        query = (
            {**base_query, **branches[0]}
            if len(branches) == 1
            else {**base_query, "$or": branches}
        )
        cursor = self.collection.find(query)
        records = await cursor.to_list(length=max(len(branches), 1) * 100)
        return [stringify_id(record) for record in records]

    def _translate_operations(self, operations: Any) -> Dict[str, Dict[str, Any]]:
        update_doc: Dict[str, Dict[str, Any]] = {}
        if isinstance(operations, dict) and "operations" in operations:
            abstract_ops = operations["operations"]
        elif isinstance(operations, list):
            abstract_ops = operations
        elif isinstance(operations, dict):
            abstract_ops = []
            for field_name, value in self._strip_system_fields(operations).items():
                if isinstance(value, dict) and "op" in value:
                    abstract_ops.append(
                        {
                            "op": value.get("op", "set"),
                            "field": field_name,
                            "value": value.get("value"),
                        }
                    )
                else:
                    abstract_ops.append(
                        {"op": "set", "field": field_name, "value": value}
                    )
        else:
            abstract_ops = [{"op": "set", "field": "value", "value": operations}]

        for operation in abstract_ops:
            if not isinstance(operation, dict):
                raise ValueError("Atomic operation must be a dictionary")

            op_name = operation.get("op", "set")
            field_name = operation.get("field")
            if not field_name:
                raise ValueError("Atomic operation requires field")

            mongo_field = self._data_field(field_name)
            if op_name == "set":
                update_doc.setdefault("$set", {})[mongo_field] = operation.get("value")
            elif op_name == "inc":
                update_doc.setdefault("$inc", {})[mongo_field] = operation.get(
                    "value", 1
                )
            elif op_name == "unset":
                update_doc.setdefault("$unset", {})[mongo_field] = ""
            else:
                raise ValueError(f"Unsupported atomic operation '{op_name}'")

        return update_doc

    def _strip_system_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in data.items()
            if key
            not in {
                "_id",
                "uuid",
                "instance_uuid",
                "template_uuid",
                "version",
                "created_at",
                "updated_at",
                "created_by",
                "updated_by",
            }
        }

    def _to_data_filter(self, search_filter: Dict[str, Any]) -> Dict[str, Any]:
        return {
            self._data_field(field_name): value
            for field_name, value in search_filter.items()
        }

    def _data_field(self, field_name: str) -> str:
        if field_name.startswith("data.") or field_name.startswith("$"):
            return field_name
        return f"data.{field_name}"

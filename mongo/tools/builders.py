# mongo/tools/builders.py

import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional


def build_template_document(
    instance_uuid: str, name: str, schema: Dict[str, Any], user_uuid: str
) -> Dict[str, Any]:
    return {
        "_id": str(uuid.uuid4()),
        "instance_uuid": str(instance_uuid),
        "name": name.strip(),
        "schema": schema,
        "version": 1,  # <- ДОБАВЬ СЮДА
        "is_deleted": False,
        "created_at": datetime.now(timezone.utc),
        "created_by": str(user_uuid),
    }


def build_record_document(
    instance_uuid: str, template_uuid: str, data: Dict[str, Any], user_uuid: str
) -> Dict[str, Any]:
    """
    Формирует системную структуру документа для коллекции records.
    Добавлено поле version со значением 1 для аудита изменений.
    """
    now = datetime.now(timezone.utc)
    return {
        "_id": str(uuid.uuid4()),
        "instance_uuid": str(instance_uuid),
        "template_uuid": str(template_uuid),
        "data": data,
        "version": 1,  # Изначальная версия записи при создании
        "is_deleted": False,
        "created_by": str(user_uuid),
        "created_at": now,
        "updated_at": now,
    }


def build_record_query(instance_uuid: str, record_uuid: str) -> Dict[str, Any]:
    """
    Строит строгий изолированный Multi-tenancy поисковый запрос для конкретной записи.
    """
    return {"_id": str(record_uuid), "instance_uuid": str(instance_uuid)}


def build_record_update_query(new_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Формирует Mongo-запрос для обновления пользовательских данных.
    Использует $inc для атомарного увеличения версии на уровне базы данных.
    """
    return {
        "$set": {"data": new_data, "updated_at": datetime.now(timezone.utc)},
        "$inc": {"version": 1},  # Атомарно увеличиваем версию на +1 в MongoDB
    }


def build_records_search_query(
    instance_uuid: str, template_uuid: str, filters: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Строит безопасный поисковый запрос с поддержкой сложных операторов ($gt, $lt, $in, $ne).
    Переводит фильтры вроде {"age": {"$gt": 25}} во внутренний формат {"data.age": {"$gt": 25}}.
    """
    query = {"instance_uuid": str(instance_uuid), "template_uuid": str(template_uuid)}

    SYSTEM_FIELDS = {
        "_id",
        "instance_uuid",
        "template_uuid",
        "version",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
    }

    # Маппим пользовательские фильтры во вложенный объект `data`
    for key, value in filters.items():
        # Защита: не даем переписать системные поля через фильтр
        if key in SYSTEM_FIELDS:
            continue

        # Формируем правильный путь к полю в MongoDB
        mongo_path = f"data.{key}"

        # Если значение — это словарь (например, {"$gt": 100} или {"$in": ["active", "pending"]})
        if isinstance(value, dict):
            # Переносим операторы без изменений, защищая вложенность
            query[mongo_path] = value
        else:
            # Для обычных точных совпадений (строки, числа, bool)
            query[mongo_path] = value

    return query


def build_records_sort_spec(
    sort_by: Optional[str], descending: bool = False
) -> Optional[List[Tuple[str, int]]]:
    """
    Преобразует имя колонки в спецификацию сортировки MongoDB с учетом вложенности.
    """
    if not sort_by:
        return None

    direction = -1 if descending else 1
    # Сортируем по полю внутри вложенного словаря data
    return [(f"data.{sort_by}", direction)]


def build_history_document(
    instance_uuid: str,
    record_uuid: str,
    user_uuid: str,
    version: int,
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Immutable audit snapshot document.
    """

    now = datetime.now(timezone.utc)

    return {
        "_id": str(uuid.uuid4()),
        "instance_uuid": str(instance_uuid),
        "record_uuid": str(record_uuid),
        "user_uuid": str(user_uuid),
        "version": int(version),
        "snapshot": snapshot,
        "is_deleted": False,
        "created_at": now,  # FIX: consistent datetime type
    }

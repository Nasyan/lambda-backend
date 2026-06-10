# mongo/tools/utils.py

from typing import Dict, Any, List, Optional
from mongo.tools.validators import validate_field_name
from datetime import datetime, timezone

ACTIVE_DOCUMENT_FILTER = {"is_deleted": {"$ne": True}}
DELETED_DOCUMENT_FILTER = {"is_deleted": True}


def with_active_filter(query: Dict[str, Any]) -> Dict[str, Any]:
    """Treat older documents without is_deleted as active."""
    return {**query, **ACTIVE_DOCUMENT_FILTER}


def with_deleted_filter(query: Dict[str, Any]) -> Dict[str, Any]:
    return {**query, **DELETED_DOCUMENT_FILTER}


def stringify_id(document: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Безопасно конвертирует MongoDB '_id' в строковый формат для API слоев.
    Изменяет переданный словарь in-place.
    """
    if document and "_id" in document:
        document["_id"] = str(document["_id"])
    return document


def stringify_ids_list(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Конвертирует '_id' в строку для списка документов."""
    for doc in documents:
        stringify_id(doc)
    return documents


def validate_dict_keys(data: Dict[str, Any]) -> None:
    """Валидирует все ключи переданного словаря на соответствие правилам имен полей."""
    for field_name in data.keys():
        validate_field_name(field_name)


def normalize_template(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Гарантирует консистентность структуры шаблона на выходе из репозитория.
    Исключает KeyError в тестах и бизнес-логике.
    """
    if not doc:
        return doc

    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    else:
        doc["_id"] = None

    doc["schema"] = doc.get("schema") or {}
    doc.setdefault("updated_by", None)
    return doc


def build_update_meta(user_uuid: Optional[str] = None) -> Dict[str, Any]:
    """Генерирует базовый словарь для обновления метаданных документов (updated_at, updated_by)."""
    meta = {"updated_at": datetime.now(timezone.utc)}
    if user_uuid:
        meta["updated_by"] = str(user_uuid)
    return meta


def extract_field_history(
    history_records: List[Dict[str, Any]], field_name: str
) -> List[Dict[str, Any]]:
    """
    Вытаскивает историю изменений конкретного поля из списка снапшотов документа.
    Чистая функция (Pure Function) для облегчения юнит-тестирования.
    """
    field_history = []

    for record in history_records:
        snapshot = record.get("snapshot", {})
        # Снимки полного документа держат поля в snapshot["data"] (задание 3);
        # плоский snapshot — legacy-формат, поддерживаем оба.
        field_source = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else snapshot

        if field_name in field_source:
            field_history.append(
                {
                    "version": record.get("version"),
                    "user_uuid": record.get("user_uuid"),
                    "updated_at": record.get("updated_at") or record.get("created_at"),
                    "field_name": field_name,
                    "value": field_source[field_name],
                }
            )

    return field_history

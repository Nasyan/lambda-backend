# mongo/tools/validators.py

from typing import Dict, Any, Optional

from mongo.tools.types import FIELD_STRATEGIES_REGISTRY
from mongo.tools.exceptions import SchemaValidationError, RecordValidationError

from mongo.tools.schema_constants import (
    ALLOWED_META_KEYS,
    ALLOWED_UI_WIDGETS,
    RESERVED_FIELD_NAMES,
    FIELD_NAME_REGEX,
)


def validate_field_name(field_name: str) -> None:
    """Проверяет корректность имени поля для Mongo/EAV схемы."""
    if not isinstance(field_name, str):
        raise SchemaValidationError("Field name must be a string")

    field_name = field_name.strip()
    if not field_name:
        raise SchemaValidationError("Field name cannot be empty")

    if "." in field_name:
        raise SchemaValidationError(f"Field name '{field_name}' cannot contain '.'")

    if field_name.startswith("$"):
        raise SchemaValidationError(f"Field name '{field_name}' cannot start with '$'")

    if "\0" in field_name:
        raise SchemaValidationError(
            f"Field name '{field_name}' cannot contain null bytes"
        )

    if field_name in RESERVED_FIELD_NAMES:
        raise SchemaValidationError(f"Field name '{field_name}' is reserved")

    if not FIELD_NAME_REGEX.match(field_name):
        raise SchemaValidationError(
            f"Invalid field name '{field_name}'. Allowed pattern: {FIELD_NAME_REGEX.pattern}"
        )


def validate_schema_definition(schema: Dict[str, Any]) -> None:
    """Валидирует структуру колонок шаблона."""
    if not isinstance(schema, dict):
        raise SchemaValidationError("Schema must be a dictionary of columns")

    for column_name, field_meta in schema.items():
        validate_field_name(column_name)

        if not isinstance(field_meta, dict):
            raise SchemaValidationError(
                f"Column '{column_name}' metadata must be a dictionary"
            )

        # 1. Проверка на неизвестные ключи
        unknown_keys = set(field_meta.keys()) - ALLOWED_META_KEYS
        if unknown_keys:
            raise SchemaValidationError(
                f"Unknown metadata keys for column '{column_name}': {sorted(unknown_keys)}. "
                f"Allowed keys: {sorted(ALLOWED_META_KEYS)}"
            )

        # --- НАЧАЛО БЛОКА ВАЛИДАЦИИ ТРИГГЕРОВ ---
        if "triggers" in field_meta:
            triggers_list = field_meta["triggers"]
            if not isinstance(triggers_list, list):
                raise SchemaValidationError(
                    f"Key 'triggers' in column '{column_name}' must be a list"
                )

            for idx, trig in enumerate(triggers_list):
                if not isinstance(trig, dict):
                    raise SchemaValidationError(
                        f"Trigger at index {idx} in column '{column_name}' must be a dictionary"
                    )

                # Сохраняем trigger_type в списке обязательных полей
                required_trigger_keys = {"trigger_id", "trigger_type", "event"}
                missing_keys = required_trigger_keys - set(trig.keys())
                if missing_keys:
                    raise SchemaValidationError(
                        f"Trigger at index {idx} in column '{column_name}' misses required keys: {missing_keys}"
                    )

                # 🔥 НОВАЯ ВАЛИДАЦИЯ: Проверка структуры условия AST, если оно передано внутри схемы
                if "condition_ast" in trig and trig["condition_ast"]:
                    from engine.ast import (
                        parse_ast,
                    )  # Импорт твоей функции парсинга Pydantic-нод

                    try:
                        parse_ast(trig["condition_ast"])
                    except Exception as e:
                        raise SchemaValidationError(
                            f"Invalid 'condition_ast' at trigger index {idx} in column '{column_name}': {str(e)}"
                        )
        # --- КОНЕЦ БЛОКА ВАЛИДАЦИИ ТРИГГЕРОВ ---

        # 2. ВАЛИДАЦИЯ UI_WIDGET
        if "ui_widget" in field_meta:
            widget_value = field_meta["ui_widget"]
            if widget_value not in ALLOWED_UI_WIDGETS:
                raise SchemaValidationError(
                    f"Invalid ui_widget '{widget_value}' for column '{column_name}'. Allowed: {sorted(ALLOWED_UI_WIDGETS)}"
                )

        if "type" not in field_meta:
            raise SchemaValidationError(
                f"Column '{column_name}' must have a 'type' attribute"
            )

        field_type = field_meta["type"]
        if field_type not in FIELD_STRATEGIES_REGISTRY:
            raise SchemaValidationError(
                f"Unknown field type '{field_type}' for column '{column_name}'. "
                f"Allowed types: {list(FIELD_STRATEGIES_REGISTRY.keys())}"
            )

        # Делегирование стратегии
        strategy = FIELD_STRATEGIES_REGISTRY[field_type]
        strategy.validate_meta(column_name, field_meta)

        if "required" in field_meta and not isinstance(field_meta["required"], bool):
            raise SchemaValidationError(
                f"'required' for column '{column_name}' must be boolean"
            )


async def validate_record_data(
    data: Dict[str, Any], schema: Dict[str, Any], s3_service: Optional[Any] = None
) -> None:
    """Проверяет пользовательские данные на соответствие динамической схеме шаблона."""
    for field_name, field_meta in schema.items():
        if field_meta.get("required", False) and field_name not in data:
            raise RecordValidationError(f"Missing required field: '{field_name}'")

    for field_name, value in data.items():
        if field_name not in schema:
            raise RecordValidationError(
                f"Field '{field_name}' is not defined in the table schema."
            )

        field_meta = schema[field_name]
        expected_type = field_meta["type"]
        strategy = FIELD_STRATEGIES_REGISTRY[expected_type]

        try:
            validated_value = await strategy.validate_data(
                value, field_meta, s3_service=s3_service
            )
        except SchemaValidationError as e:
            raise RecordValidationError(
                f"Field '{field_name}' validation failed for type '{expected_type}': {str(e)}"
            ) from e

        data[field_name] = validated_value

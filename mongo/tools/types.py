# mongo/tools/types.py

import datetime
import re
from typing import Any, Dict
from uuid import UUID
from mongo.tools.exceptions import SchemaValidationError
from engine.ast import parse_ast
from engine.exceptions.evaluator import FormulaValidationError

# Регулярные выражения для валидации URL и Телефона
URL_REGEX = re.compile(
    r"^(?:http|ftp)s?://"  # http:// или https://
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"  # домен...
    r"localhost|"  # localhost...
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # ...или ip
    r"(?::\d+)?"  # опциональный порт
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)

# Международный формат E.164: от 7 до 15 цифр, опциональный плюс в начале
PHONE_REGEX = re.compile(r"^\+?[1-9]\d{6,14}$")


class BaseFieldStrategy:
    """Базовый класс для всех типов полей в CRM."""

    @staticmethod
    def validate_meta(column_name: str, field_meta: Dict[str, Any]) -> None:
        """Валидация настроек самой колонки (запускается при создании/изменении таблицы)."""
        pass

    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> Any:
        """Валидация значения в ячейке (запускается при записи строки)."""
        return value


class StringField(BaseFieldStrategy):
    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> str:
        if not isinstance(value, str):
            raise SchemaValidationError("Must be a string")
        return str(value)


class NumberField(BaseFieldStrategy):
    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise SchemaValidationError("Must be a number")
        return float(value)


class BooleanField(BaseFieldStrategy):
    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> bool:
        if not isinstance(value, bool):
            raise SchemaValidationError("Must be a boolean")
        return value


class CheckboxField(BaseFieldStrategy):
    """Специализированное поле-переключатель (чекбокс) для фронтенда."""

    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> bool:
        if not isinstance(value, bool):
            raise SchemaValidationError("Checkbox value must be a boolean (True/False)")
        return value


class SelectField(BaseFieldStrategy):
    @staticmethod
    def validate_meta(column_name: str, field_meta: Dict[str, Any]) -> None:
        if "options" not in field_meta:
            raise SchemaValidationError(
                f"Column '{column_name}' of type 'select' must have an 'options' attribute"
            )

        options = field_meta["options"]

        if not isinstance(options, list) or not all(
            isinstance(opt, str) for opt in options
        ):
            raise SchemaValidationError(
                f"'options' for column '{column_name}' must be a non-empty list of strings"
            )

        if not options:
            raise SchemaValidationError(
                f"'options' list for column '{column_name}' cannot be empty"
            )

        if len(options) != len(set(options)):
            raise SchemaValidationError(
                f"Options for column '{column_name}' must be unique"
            )

    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> str:
        if not isinstance(value, str):
            raise SchemaValidationError("Selected variant must be a string")

        allowed_options = field_meta.get("options", [])
        if value not in allowed_options:
            raise SchemaValidationError(
                f"Value '{value}' is not allowed. Allowed options: {allowed_options}"
            )
        return value


class ImageField(BaseFieldStrategy):
    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> str:
        if not isinstance(value, str):
            raise SchemaValidationError("Image reference must be a string path")

        s3_service = kwargs.get("s3_service")
        if not s3_service:
            raise SchemaValidationError(
                "Internal error: S3 storage service is unavailable for validation"
            )

        file_exists = await s3_service.file_exists(value)
        if not file_exists:
            raise SchemaValidationError(
                f"The uploaded file at path '{value}' does not exist in S3 storage"
            )

        return value


class FormulaField(BaseFieldStrategy):
    @staticmethod
    def validate_meta(column_name: str, field_meta: Dict[str, Any]) -> None:
        if "ast" not in field_meta:
            raise SchemaValidationError(
                f"Column '{column_name}' of type 'formula' must have an 'ast' attribute"
            )

        raw_ast = field_meta["ast"]
        if not raw_ast or not isinstance(raw_ast, dict):
            raise SchemaValidationError(
                f"The 'ast' attribute for column '{column_name}' must be a non-empty dictionary"
            )

        try:
            parse_ast(raw_ast)
        except FormulaValidationError as e:
            raise SchemaValidationError(
                f"Invalid formula AST structure in column '{column_name}': {str(e)}"
            )

    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> Any:
        return value


class DateTimeField(BaseFieldStrategy):
    """Поле даты и времени. Поддерживает автозаполнение 'now'."""

    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> str:
        if value == "now":
            return datetime.datetime.now(datetime.timezone.utc).isoformat()

        if not isinstance(value, str):
            raise SchemaValidationError(
                "Datetime value must be an ISO 8601 string or 'now'"
            )

        try:
            datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise SchemaValidationError(
                f"Invalid datetime format: '{value}'. Expected ISO 8601 format."
            )
        return value


class UrlField(BaseFieldStrategy):
    """Поле веб-ссылки."""

    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> str:
        if not isinstance(value, str):
            raise SchemaValidationError("URL must be a string")

        if not re.match(URL_REGEX, value):
            raise SchemaValidationError(f"Invalid URL format: '{value}'")

        return value


class PhoneField(BaseFieldStrategy):
    """Поле номера телефона в международном формате."""

    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> str:
        if not isinstance(value, str):
            raise SchemaValidationError("Phone number must be a string")

        clean_value = (
            value.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        )

        if not re.match(PHONE_REGEX, clean_value):
            raise SchemaValidationError(
                f"Invalid international phone format: '{value}'. Example: +375291234567"
            )

        return clean_value


class RelationListField(BaseFieldStrategy):
    """
    🚀 НОВОЕ ПОЛЕ: Хранение динамического массива связей (чипсы / тэги / корзина продуктов).
    Каждый элемент в списке — это объект, обязательно содержащий 'target_uuid' (ID сущности),
    и любые дополнительные поля метаданных (количество, цена среза на момент заказа и т.д.).
    """

    @staticmethod
    def validate_meta(column_name: str, field_meta: Dict[str, Any]) -> None:
        if "target_template_uuid" not in field_meta:
            raise SchemaValidationError(
                f"Column '{column_name}' of type 'relation_list' must have a 'target_template_uuid' attribute"
            )

    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> list:
        if not isinstance(value, list):
            raise SchemaValidationError(
                "Value for relation_list must be an array (list) of objects"
            )

        record_repo = kwargs.get("record_repo") or kwargs.get("mongo_repo")
        instance_uuid = kwargs.get("instance_uuid")
        target_template_uuid = field_meta["target_template_uuid"]
        if not record_repo:
            raise SchemaValidationError(
                "Internal error: record repository is unavailable for relation_list validation"
            )
        if not instance_uuid:
            raise SchemaValidationError(
                "Internal error: instance_uuid is unavailable for relation_list validation"
            )

        validated_list = []
        target_uuids: set[str] = set()
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise SchemaValidationError(
                    f"Item at index {idx} must be an object (dictionary)"
                )

            if "target_uuid" not in item:
                raise SchemaValidationError(
                    f"Item at index {idx} is missing required key 'target_uuid'"
                )

            target_uuid = item["target_uuid"]
            if not isinstance(target_uuid, str):
                raise SchemaValidationError(
                    f"Item at index {idx} target_uuid must be a UUID string"
                )

            try:
                UUID(target_uuid)
            except ValueError as exc:
                raise SchemaValidationError(
                    f"Item at index {idx} target_uuid has invalid UUID format: '{target_uuid}'"
                ) from exc

            target_uuids.add(target_uuid)
            validated_list.append(item)

        if target_uuids:
            existing_records = await record_repo.get_records_by_uuids(
                instance_uuid=str(instance_uuid),
                record_uuids=list(target_uuids),
                template_uuid=str(target_template_uuid),
            )
            existing_uuids = set(existing_records.keys())

            if len(existing_uuids) != len(target_uuids):
                missing_uuids = sorted(target_uuids - existing_uuids)
                raise SchemaValidationError(
                    f"Linked record(s) not found in template '{target_template_uuid}': {missing_uuids}"
                )

        return validated_list


class CascadingTreeField(BaseFieldStrategy):
    """
    🚀 НОВОЕ ПОЛЕ: Каскадное дерево атрибутов (Hierarchical Tags).
    Хранит структуру тегов переменной длины и содержания.
    В БД (data) сохраняется в виде плоского словаря: {"Тип": "Брошь", "Материал": "Дерево"}.
    """

    @staticmethod
    def validate_meta(column_name: str, field_meta: Dict[str, Any]) -> None:
        if "tree_config" not in field_meta:
            raise SchemaValidationError(
                f"Column '{column_name}' of type 'cascading_tree' must have a 'tree_config' attribute"
            )

        # Рекурсивная валидация графа (дерева)
        def _validate_node(node: Any, depth: int = 0) -> None:
            if depth > 20:  # Защита от бесконечной рекурсии и переполнения
                raise SchemaValidationError(
                    f"Tree in '{column_name}' is too deep (max 20 levels)"
                )
            if not isinstance(node, dict):
                raise SchemaValidationError("Tree node must be a dictionary")

            if "floor_name" not in node or not isinstance(node["floor_name"], str):
                raise SchemaValidationError("Each node must have a string 'floor_name'")

            if "options" not in node or not isinstance(node["options"], dict):
                raise SchemaValidationError(
                    f"Node '{node.get('floor_name')}' must have an 'options' dictionary"
                )

            # Проверка флага для фронтенда (адаптивный/фиксированный)
            node_type = node.get("type")
            if node_type and node_type not in ["adaptive", "fixed"]:
                raise SchemaValidationError(
                    f"Node '{node['floor_name']}' type must be 'adaptive' or 'fixed'"
                )

            if not node["options"]:
                raise SchemaValidationError(
                    f"Options for '{node['floor_name']}' cannot be empty"
                )

            for opt_key, next_node in node["options"].items():
                if not isinstance(opt_key, str):
                    raise SchemaValidationError("Option keys must be strings")

                # Если ветка продолжается (не лист дерева), валидируем следующий этаж
                if next_node is not None:
                    _validate_node(next_node, depth + 1)

        _validate_node(field_meta["tree_config"])

    @staticmethod
    async def validate_data(
        value: Any, field_meta: Dict[str, Any], **kwargs: Any
    ) -> Dict[str, str]:
        if not isinstance(value, dict):
            raise SchemaValidationError(
                "Cascading tree value must be a dictionary of selected tags (e.g. {'Type': 'Ring'})"
            )

        current_node = field_meta["tree_config"]
        validated_path = {}

        # Идем по дереву, сверяясь с тем, что прислал пользователь
        while current_node is not None:
            floor_name = current_node["floor_name"]

            if floor_name not in value:
                raise SchemaValidationError(
                    f"Missing selection for mandatory floor: '{floor_name}'"
                )

            chosen_option = value[floor_name]

            if chosen_option not in current_node["options"]:
                allowed = list(current_node["options"].keys())
                raise SchemaValidationError(
                    f"Invalid option '{chosen_option}' for floor '{floor_name}'. Allowed: {allowed}"
                )

            # Записываем успешно пройденный шаг
            validated_path[floor_name] = chosen_option

            # Спускаемся на этаж ниже (в зависимости от выбора)
            current_node = current_node["options"][chosen_option]

        # Строгая защита: проверяем, что юзер не докинул лишних (мусорных) этажей в JSON
        extra_keys = set(value.keys()) - set(validated_path.keys())
        if extra_keys:
            raise SchemaValidationError(
                f"Extra invalid floors provided in cascade: {list(extra_keys)}"
            )

        return validated_path


# ---------------------------------------------------------------------
# Реестр стратегий (добавляем новые типы сюда)
# ---------------------------------------------------------------------
FIELD_STRATEGIES_REGISTRY: Dict[str, type[BaseFieldStrategy]] = {
    "string": StringField,
    "number": NumberField,
    "boolean": BooleanField,
    "checkbox": CheckboxField,
    "select": SelectField,
    "image": ImageField,
    "formula": FormulaField,
    "datetime": DateTimeField,
    "url": UrlField,
    "phone": PhoneField,
    "relation_list": RelationListField,  # 🔥 Регистрируем стратегию поля списка связей
    "cascading_tree": CascadingTreeField,
}

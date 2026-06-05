# triggers/schemas.py

from typing import Optional, Union, Dict, Any, List
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from uuid import UUID
from engine.ast import parse_ast
from engine.exceptions.evaluator import FormulaValidationError
from triggers.exceptions.validation import RecordValidationError
from triggers.models import TriggerType, EventType, PayloadReturnType


class TestActionSchema(BaseModel):
    """Требования для тестовой функции"""

    required_text: str = Field(..., description="Тестовый текст, который обязателен")
    send_attempts: int = Field(default=1, description="Количество симулируемых попыток")


class TGBroadcastParams(BaseModel):
    """Требования для массовой рассылки в Telegram"""

    target_phone_column: str = Field(
        ..., description="Название поля в Mongo, где лежит телефон/TG ID"
    )
    message_template: str = Field(..., description="Текст сообщения для рассылки")


class NotificationParams(BaseModel):
    """
    Параметры для связи триггера с шаблоном уведомлений CRM.
    Триггер больше не хранит текст, только ссылку на сущность шаблона.
    """

    notification_template_uuid: UUID = Field(
        ..., description="UUID связанного шаблона уведомлений из Postgres"
    )


# Дополнительные схемы для Mongo-операций (для обеспечения строгой типизации Union)
class MongoInsertParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    payload: Dict[str, Any] = Field(default_factory=dict)


class MongoUpdateParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    filter: Dict[str, Any] = Field(default_factory=dict)
    update_op: Dict[str, Any] = Field(default_factory=dict)


class MongoUpsertParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    search_fields: List[str]
    payload: Dict[str, Any] = Field(default_factory=dict)


# Объединяем все возможные типы параметров экшенов
ActionParamsType = Union[
    TestActionSchema,
    TGBroadcastParams,
    NotificationParams,
    MongoInsertParams,
    MongoUpdateParams,
    MongoUpsertParams,
    Dict[str, Any],
]


class TriggerCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Orders -> Clients upsert",
                    "trigger_type": "AUTOMATION",
                    "event_type": "ON_RECORD_CREATE",
                    "source_template_uuid": "00000000-0000-0000-0000-000000000001",
                    "target_template_uuid": "00000000-0000-0000-0000-000000000002",
                    "condition_ast": {
                        "type": "binary_op",
                        "operator": "gt",
                        "left": {"type": "field", "value": "client_phone"},
                        "right": {"type": "literal", "value": ""},
                    },
                    "payload_ast": {
                        "type": "object",
                        "fields": {
                            "phone": {"type": "field", "value": "client_phone"},
                            "name": {"type": "field", "value": "client_name"},
                        },
                    },
                    "action_name": "UPSERT_RECORD",
                    "action_params": {
                        "search_fields": ["phone"],
                    },
                    "action_mapping_ast": {
                        "type": "object",
                        "fields": {
                            "phone": {"type": "field", "value": "client_phone"},
                            "name": {"type": "field", "value": "client_name"},
                        },
                    },
                },
                {
                    "name": "Product live suggestions",
                    "trigger_type": "LIVE_EVAL",
                    "event_type": "MANUAL",
                    "source_template_uuid": "00000000-0000-0000-0000-000000000003",
                    "target_template_uuid": "00000000-0000-0000-0000-000000000003",
                    "condition_ast": {
                        "type": "binary_op",
                        "operator": "gt",
                        "left": {"type": "input"},
                        "right": {"type": "literal", "value": ""},
                    },
                    "payload_ast": {
                        "type": "query",
                        "target_template_uuid": "00000000-0000-0000-0000-000000000003",
                        "filters": [
                            {
                                "field": "name",
                                "operator": "contains",
                                "value": {"type": "input"},
                            },
                            {
                                "field": "quantity_left",
                                "operator": "gt",
                                "value": {"type": "literal", "value": 0},
                            },
                        ],
                        "return_fields": ["name", "quantity_left"],
                    },
                    "action_name": "RETURN_TO_CALLER",
                },
                {
                    "name": "Paid order stock decrement",
                    "trigger_type": "AUTOMATION",
                    "event_type": "ON_RECORD_UPDATE",
                    "source_template_uuid": "00000000-0000-0000-0000-000000000001",
                    "target_template_uuid": "00000000-0000-0000-0000-000000000003",
                    "condition_ast": {
                        "type": "binary_op",
                        "operator": "eq",
                        "left": {"type": "field", "value": "payment"},
                        "right": {"type": "literal", "value": "картой"},
                    },
                    "payload_ast": {"type": "field", "value": "product_list"},
                    "action_name": "UPDATE_RECORD",
                    "action_mapping_ast": {
                        "type": "object",
                        "fields": {
                            "_id": {
                                "type": "field",
                                "value": "current_item.target_uuid",
                            },
                            "quantity_left": {
                                "type": "object",
                                "fields": {
                                    "op": {"type": "literal", "value": "inc"},
                                    "value": {
                                        "type": "binary_op",
                                        "operator": "multiply",
                                        "left": {
                                            "type": "field",
                                            "value": "current_item.qty",
                                        },
                                        "right": {"type": "literal", "value": -1},
                                    },
                                },
                            },
                        },
                    },
                },
            ]
        }
    )

    name: str = Field(..., max_length=255, description="Название триггера")
    trigger_type: TriggerType = Field(default=TriggerType.LIVE_EVAL)
    condition_ast: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "AST условия триггера. Если задано, должно возвращать BOOLEAN."
        ),
    )
    payload_ast: Dict[str, Any] = Field(
        ...,
        description=(
            "AST извлечения payload. Тип результата вычисляется сервером."
        ),
    )
    action_mapping_ast: Optional[Dict[str, Any]] = Field(
        default=None,
        description="AST маппинга данных для экшенов, которым он нужен.",
    )
    source_template_uuid: UUID
    target_template_uuid: UUID
    target_field: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Название целевой колонки (поля) в шаблоне",
    )

    # Поля для AUTOMATION
    event_type: Optional[EventType] = None
    cron_expression: Optional[str] = None
    action_name: Optional[str] = None
    action_params: Optional[ActionParamsType] = None

    @field_validator("condition_ast", "payload_ast", "action_mapping_ast")
    @classmethod
    def validate_ast_tree(cls, value, info: ValidationInfo):
        if value is None:
            return value
        try:
            parse_ast(value)
        except (FormulaValidationError, ValidationError) as exc:
            raise RecordValidationError(
                field=info.field_name,
                expected="valid AST",
                got=value,
                detail=str(exc),
            ) from exc
        return value

    @model_validator(mode="before")
    def validate_automation_fields(cls, values):
        trigger_type = values.get("trigger_type")
        if trigger_type == TriggerType.AUTOMATION.value:
            if not values.get("event_type"):
                raise ValueError(
                    "Для AUTOMATION необходимо указать event_type (например, MANUAL или CRON)"
                )
            if not values.get("action_name"):
                raise ValueError(
                    "Для AUTOMATION необходимо указать action_name (например, create_crm_notification)"
                )

            if values.get("event_type") == EventType.CRON.value and not values.get(
                "cron_expression"
            ):
                raise ValueError("Для события CRON необходимо указать cron_expression")

            # Глубокая валидация параметров под конкретный экшен уведомлений
            action_name = values.get("action_name")
            action_params = values.get("action_params") or {}

            if action_name == "create_crm_notification":
                if "notification_template_uuid" not in action_params:
                    raise ValueError(
                        "Для экшена 'create_crm_notification' параметр 'notification_template_uuid' является строго обязательным."
                    )
        return values


class TriggerUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    trigger_type: Optional[TriggerType] = None
    condition_ast: Optional[Dict[str, Any]] = None
    payload_ast: Optional[Dict[str, Any]] = None
    action_mapping_ast: Optional[Dict[str, Any]] = None
    source_template_uuid: Optional[UUID] = None
    target_template_uuid: Optional[UUID] = None
    target_field: Optional[str] = Field(default=None, max_length=64)
    event_type: Optional[EventType] = None
    cron_expression: Optional[str] = None
    action_name: Optional[str] = None
    action_params: Optional[ActionParamsType] = None

    @field_validator("condition_ast", "payload_ast", "action_mapping_ast")
    @classmethod
    def validate_optional_ast_tree(cls, value, info: ValidationInfo):
        if value is None:
            return value
        try:
            parse_ast(value)
        except (FormulaValidationError, ValidationError) as exc:
            raise RecordValidationError(
                field=info.field_name,
                expected="valid AST",
                got=value,
                detail=str(exc),
            ) from exc
        return value


class TriggerResponse(BaseModel):
    id: UUID
    instance_uuid: UUID
    name: str
    trigger_type: TriggerType
    condition_ast: Optional[Dict[str, Any]]
    payload_ast: Dict[str, Any]
    payload_return_type: PayloadReturnType
    action_mapping_ast: Optional[Dict[str, Any]]
    source_template_uuid: UUID
    target_template_uuid: Optional[UUID]
    target_field: Optional[str] = None
    event_type: Optional[EventType]
    cron_expression: Optional[str]
    action_name: Optional[str]
    action_params: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True


class TriggerEvaluateRequest(BaseModel):
    context_data: Dict[str, Any]
    manual_input: Optional[Any] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "context_data": {},
                    "manual_input": "клавиатура",
                }
            ]
        }
    )

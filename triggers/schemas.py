# triggers/schemas.py

from typing import Optional, Union, Dict, Any, List
from pydantic import BaseModel, Field, model_validator
from uuid import UUID
from triggers.models import TriggerType, EventType


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
    target_template_uuid: UUID
    payload: Dict[str, Any] = Field(default_factory=dict)


class MongoUpdateParams(BaseModel):
    target_template_uuid: UUID
    filter: Dict[str, Any] = Field(default_factory=dict)
    update_op: Dict[str, Any] = Field(default_factory=dict)


class MongoUpsertParams(BaseModel):
    target_template_uuid: UUID
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
    name: str = Field(..., max_length=255, description="Название триггера")
    trigger_type: TriggerType = Field(default=TriggerType.LIVE_EVAL)
    ast: Dict[str, Any] = Field(
        ..., description="JSON структура AST дерева (условие или формула)"
    )
    target_template_uuid: Optional[UUID] = None
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


class TriggerResponse(BaseModel):
    id: UUID
    instance_uuid: UUID
    name: str
    trigger_type: TriggerType
    ast: Dict[str, Any]
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

# core/schemas/template.py

from pydantic import BaseModel, ConfigDict, Field
from typing import Dict, Any, List, Optional
from datetime import datetime


class TriggerMetaResponse(BaseModel):
    trigger_id: str = Field(..., description="UUID триггера из базы PostgreSQL")
    trigger_type: str = Field(..., description="Тип триггера, например: LIVE_EVAL")
    event: str = Field(..., description="Событие на фронтенде, например: onChange")
    target_field: Optional[str] = Field(
        None, description="Системное имя целевого поля для записи результата"
    )

    model_config = {"from_attributes": True}


class ColumnMetaResponse(BaseModel):
    type: str
    required: bool = False
    ast: Optional[Dict[str, Any]] = None
    options: Optional[Any] = None
    triggers: List[TriggerMetaResponse] = Field(default_factory=list)
    ui_widget: Optional[str] = Field(
        None, description="UI-виджет для фронтенда (например: 'qr', 'camera_capture')"
    )

    model_config = {"extra": "allow", "from_attributes": True}


class TemplateCreateRequest(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Название таблицы (например, 'Клиенты')",
    )
    schema_definition: Dict[str, Any] = Field(
        ...,
        alias="schema",
        description="JSON-схема, описывающая поля таблицы (их типы, обязательность и т.д.)",
    )

    model_config = ConfigDict(populate_by_name=True)


class TemplateResponse(BaseModel):
    id: str = Field(alias="_id")  # Автоматически смапит _id в id при сериализации
    instance_uuid: str
    name: str
    schema_definition: Dict[str, ColumnMetaResponse] = Field(
        alias="schema"
    )  # Мапит schema в schema_definition со структурированными триггерами
    created_by: str
    updated_by: Optional[str] = None
    version: int = 1
    is_deleted: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {
        "populate_by_name": True,  # Разрешает передавать данные как по алиасу (_id), так и по имени (id)
        "from_attributes": True,  # Позволяет работать с объектами и словарями
    }


class TemplateUpdateMetadataRequest(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=100, description="Новое название таблицы"
    )


class ColumnAddOrUpdateRequest(BaseModel):
    column_name: str = Field(
        ..., min_length=1, max_length=64, description="Системное имя колонки"
    )
    field_meta: Dict[str, Any] = Field(
        ...,
        description="Метаданные поля, например: {'type': 'string', 'required': true}",
    )

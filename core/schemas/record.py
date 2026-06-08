# core/schemas/record.py

from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List


class RecordCreateRequest(BaseModel):
    data: Dict[str, Any] = Field(
        ..., description="Данные записи, соответствующие схеме шаблона"
    )


class RecordUpdateRequest(BaseModel):
    data: Dict[str, Any] = Field(..., description="Новые данные записи")


class RecordResponse(BaseModel):
    id: str = Field(..., alias="_id")
    instance_uuid: str
    template_uuid: str
    data: Dict[str, Any]
    created_by: str
    updated_by: Optional[str] = None
    version: int
    is_deleted: bool = False

    class Config:
        populate_by_name = True


class PaginatedRecordsResponse(BaseModel):
    total: int  # Всего записей по данным фильтрам в БД
    limit: int  # Сколько запросили
    offset: int  # Сколько пропустили
    results: List[RecordResponse]  # Список самих записей

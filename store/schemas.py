# store/schemas.py

from pydantic import BaseModel, Field
from typing import Dict, Any, List


class StorefrontSchemaResponse(BaseModel):
    template_name: str
    fields: Dict[str, Any] = Field(
        description="Схема таблицы (только разрешенные поля)"
    )


class StorefrontRecordCreateRequest(BaseModel):
    data: Dict[str, Any] = Field(
        ...,
        description="Данные для создания записи (будут отфильтрованы по write_mask)",
    )


class StorefrontRecordResponse(BaseModel):
    id: str = Field(..., alias="_id")
    data: Dict[str, Any]

    class Config:
        populate_by_name = True


class StorefrontPaginatedRecordsResponse(BaseModel):
    total: int
    limit: int
    offset: int
    results: List[StorefrontRecordResponse]

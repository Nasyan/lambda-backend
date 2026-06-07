# analytics/schemas.py

from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator
from uuid import UUID
from fastapi import HTTPException, status

from analytics.models import WidgetType, AggregationFunction
from engine.exceptions.evaluator import FormulaValidationError
from engine.ast import parse_ast


class AxisXConfig(BaseModel):
    field: str = Field(..., description="Имя системной колонки шаблона")
    type: str = Field(..., description="categorical, datetime или numerical")
    date_bucket: Optional[str] = Field(
        None, description="day, week, month, year (только если type=datetime)"
    )


class AxisYConfig(BaseModel):
    field: str = Field(
        ...,
        description="Имя колонки для подсчета метрики (для COUNT можно передать '_id')",
    )
    aggregation: AggregationFunction


class ChartConfigPayload(BaseModel):
    axis_x: AxisXConfig
    axis_y: AxisYConfig
    unwind_field: Optional[str] = Field(
        None,
        description="Имя массива для развертывания через $unwind (поддержка RelationListField)",
    )


class WidgetCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    target_template_uuid: str
    widget_type: WidgetType  # Изменили str на WidgetType enum для строгой валидации
    chart_config: (
        ChartConfigPayload  # 🔥 ИСПРАВЛЕНО: заменили Any на правильную подмодель
    )
    ast_filter: Optional[Dict[str, Any]] = None

    @field_validator("ast_filter")
    @classmethod
    def validate_ast_structure(
        cls, v: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        if v is not None:
            try:
                # Вызываем функцию парсинга движка
                parse_ast(v)
            except FormulaValidationError as e:
                # 🔥 ИСПРАВЛЕНО: перехватываем ошибку движка и превращаем в HTTP 400 Bad Request
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid formula structure: {str(e)}",
                )
        return v


class WidgetResponse(BaseModel):
    id: UUID
    instance_uuid: UUID
    name: str
    target_template_uuid: UUID
    widget_type: WidgetType
    ast_filter: Optional[Dict[str, Any]]
    chart_config: ChartConfigPayload

    model_config = ConfigDict(from_attributes=True)


class WidgetUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    widget_type: Optional[WidgetType] = None
    ast_filter: Optional[Dict[str, Any]] = None
    chart_config: Optional[ChartConfigPayload] = None

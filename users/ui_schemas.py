import enum
from typing import List, Dict
from uuid import UUID
from pydantic import BaseModel, Field, model_validator


class KitItemType(str, enum.Enum):
    TEMPLATE = "template"
    ANALYTICS = "analytics"


class KitItemSubtype(str, enum.Enum):
    NOTES = "notes"
    WORKFLOW = "workflow"
    TABLES = "tables"
    NONE = "none"


TYPE_SUBTYPE_MAPPING: Dict[KitItemType, List[KitItemSubtype]] = {
    KitItemType.TEMPLATE: [
        KitItemSubtype.NOTES,
        KitItemSubtype.WORKFLOW,
        KitItemSubtype.TABLES,
    ],
    KitItemType.ANALYTICS: [
        KitItemSubtype.NONE,
    ],
}


class PositionSchema(BaseModel):
    x: int = Field(..., description="Координата X на сетке рабочего стола")
    y: int = Field(..., description="Координата Y на сетке рабочего стола")


class UiKitItemSchema(BaseModel):
    uuid: UUID = Field(..., description="UUID сущности (таблицы или отчета)")
    type: KitItemType = Field(..., description="Тип компонента")
    subtype: KitItemSubtype = Field(
        default=KitItemSubtype.NONE, description="Подтип инструмента"
    )
    position: PositionSchema = Field(..., description="Позиция компонента на фронтенде")

    @model_validator(mode="after")
    def validate_type_subtype_match(self) -> "UiKitItemSchema":
        allowed_subtypes = TYPE_SUBTYPE_MAPPING.get(self.type, [])

        if self.subtype not in allowed_subtypes:
            allowed_names = [st.value for st in allowed_subtypes]
            raise ValueError(
                f"Невалидный subtype '{self.subtype.value}' для типа '{self.type.value}'. "
                f"Допустимые подтипы для этого типа: {allowed_names}"
            )

        return self


class UiKitSchema(BaseModel):
    """
    Основной контейнер для списка избранных элементов в UI.
    Именно этот класс используется для инициализации данных из БД.
    """

    favorites: List[UiKitItemSchema] = Field(default_factory=list)

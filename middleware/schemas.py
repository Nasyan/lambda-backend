# middleware/schemas.py

from typing import Optional, Any
from pydantic import BaseModel, Field
from sqlalchemy import asc, desc


class ListParameters(BaseModel):
    """Единый контракт для всех списочных эндпоинтов платформы."""

    search: Optional[str] = Field(
        None, description="Строка для поиска (регистронезависимая)"
    )
    sort_by: Optional[str] = Field(
        "created_at:desc", description="Формат: field_name:asc|desc"
    )

    def get_mongo_sort(self, default_field: str = "created_at") -> tuple[str, int]:
        """Парсинг сортировки для MongoDB."""
        if not self.sort_by or ":" not in self.sort_by:
            return default_field, -1
        field, direction = self.sort_by.split(":", 1)
        direction_val = -1 if direction.lower() == "desc" else 1
        return field, direction_val

    def get_postgres_sort(self, model: Any, default_field: str = "created_at") -> Any:
        """
        Парсинг сортировки для SQLAlchemy (PostgreSQL).
        Принимает класс модели SQLAlchemy (например, Trigger).
        Возвращает объект сортировки (например, desc(Trigger.created_at)).
        """
        if not self.sort_by or ":" not in self.sort_by:
            # Если сортировка не передана, берем дефолтное поле модели
            fallback_col = getattr(model, default_field, model.created_at)
            return desc(fallback_col)

        field_name, direction = self.sort_by.split(":", 1)

        # Безопасно достаем атрибут колонки из модели, чтобы избежать SQL-инъекций
        column = getattr(model, field_name, None)
        if column is None:
            # Если фронтенд прислал несуществующее поле, падаем на дефолт
            column = getattr(model, default_field, model.created_at)

        return desc(column) if direction.lower() == "desc" else asc(column)

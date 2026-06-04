# analytics/models.py

import uuid
from enum import Enum
from sqlalchemy import Column, String, JSON, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from database.db import Base


class WidgetType(str, Enum):
    LINE = "LINE"  # Линейный график (динамика по времени)
    BAR = "BAR"  # Столбчатая диаграмма (сравнение категорий)
    PIE = "PIE"  # Круговая диаграмма (доли/проценты структур)
    KPI_CARD = "KPI_CARD"  # Одиночная карточка с ключевой метрикой


class AggregationFunction(str, Enum):
    SUM = "SUM"
    COUNT = "COUNT"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


class AnalyticsWidget(Base):
    __tablename__ = "analytics_widgets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_uuid = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String(255), nullable=False)

    # К какому динамическому шаблону (коллекции Mongo) привязан график
    target_template_uuid = Column(UUID(as_uuid=True), nullable=False, index=True)

    widget_type = Column(
        SQLEnum(WidgetType, name="widget_type_enum", create_type=True),
        nullable=False,
        default=WidgetType.BAR,
    )

    # AST-фильтр (точно такой же, как в триггерах)
    # Позволяет строить графики по сегменту данных (например, "Только закрытые заказы")
    ast_filter = Column(JSON, nullable=True)

    # Специфичный конфиг осей X и Y, группировок и типов агрегации
    # Структура: {"axis_x": {"field": "status", "type": "categorical"}, "axis_y": {"field": "_id", "aggregation": "COUNT"}}
    chart_config = Column(JSON, nullable=False)

    def to_dict(self):
        return {
            "id": str(self.id),
            "instance_uuid": str(self.instance_uuid),
            "name": self.name,
            "target_template_uuid": str(self.target_template_uuid),
            "widget_type": self.widget_type.value,
            "ast_filter": self.ast_filter,
            "chart_config": self.chart_config,
        }

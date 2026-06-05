# analytics/widget.py

from uuid import UUID
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.models import AnalyticsWidget
from analytics.repository import AnalyticsWidgetRepository
from analytics.schemas import WidgetCreateRequest, WidgetUpdateRequest
from mongo.analytics import AnalyticsRepository
from analytics.schemas import ChartConfigPayload

# Импортируем чистое доменное исключение
from analytics.exceptions import WidgetNotFoundError


class WidgetService:
    """
    Оркестратор аналитических дашбордов.
    Полностью очищен от логики MongoDB-компилятора и веб-зависимостей (HTTPException).
    Делегирует сборку запросов в MongoPipelineBuilder.
    """

    @classmethod
    def _raise_widget_not_found(cls, widget_uuid: UUID, instance_uuid: UUID) -> None:
        """Вспомогательный метод для стандартизации вызова ошибки."""
        raise WidgetNotFoundError(widget_uuid=widget_uuid, instance_uuid=instance_uuid)

    @classmethod
    async def create_widget(
        cls, instance_uuid: UUID, payload: WidgetCreateRequest, db: AsyncSession
    ) -> AnalyticsWidget:
        widget = AnalyticsWidget(
            instance_uuid=instance_uuid,
            name=payload.name,
            target_template_uuid=payload.target_template_uuid,
            widget_type=payload.widget_type,
            ast_filter=payload.ast_filter,
            chart_config=payload.chart_config.model_dump(),
        )
        AnalyticsWidgetRepository(db).add(widget)
        await db.commit()
        await db.refresh(widget)
        return widget

    @classmethod
    async def get_widget_data(
        cls, widget_uuid: UUID, instance_uuid: UUID, db: AsyncSession, mongo_db: Any
    ) -> List[Dict[str, Any]]:
        # 1. Достаем метаданные графика из Postgres
        widget = await AnalyticsWidgetRepository(db).get(instance_uuid, widget_uuid)
        if not widget:
            cls._raise_widget_not_found(widget_uuid, instance_uuid)

        # 2. Инициализируем аналитический слой MongoDB
        analytics_repo = AnalyticsRepository(mongo_db)

        chart_config_model = ChartConfigPayload(**widget.chart_config)

        schema_definition = await analytics_repo.get_schema_definition(
            str(widget.target_template_uuid)
        )
        chart_config_model = ChartConfigPayload(**widget.chart_config)

        data = await analytics_repo.get_chart_data(
            instance_uuid=str(widget.instance_uuid),
            template_uuid=str(widget.target_template_uuid),
            config=chart_config_model,
            schema_definition=schema_definition,  # 🔥 Передаем схему сюда
            ast_filter=widget.ast_filter,
        )

        return data

    @classmethod
    async def update_widget(
        cls,
        widget_uuid: UUID,
        instance_uuid: UUID,
        payload: WidgetUpdateRequest,
        db: AsyncSession,
    ) -> AnalyticsWidget:
        widget = await AnalyticsWidgetRepository(db).get(instance_uuid, widget_uuid)
        if not widget:
            cls._raise_widget_not_found(widget_uuid, instance_uuid)

        update_data = payload.model_dump(exclude_unset=True)

        if "chart_config" in update_data and update_data["chart_config"] is not None:
            update_data["chart_config"] = payload.chart_config.model_dump()

        for key, value in update_data.items():
            setattr(widget, key, value)

        await db.commit()
        await db.refresh(widget)
        return widget

    @classmethod
    async def delete_widget(
        cls, widget_uuid: UUID, instance_uuid: UUID, db: AsyncSession
    ) -> None:
        widget = await AnalyticsWidgetRepository(db).get(instance_uuid, widget_uuid)
        if not widget:
            cls._raise_widget_not_found(widget_uuid, instance_uuid)

        await AnalyticsWidgetRepository(db).delete(widget)
        await db.commit()

# analytics/widget.py

from uuid import UUID
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.models import AnalyticsWidget
from analytics.repository import AnalyticsWidgetRepository
from analytics.schemas import WidgetCreateRequest, WidgetUpdateRequest
from mongo.analytics import AnalyticsRepository
from analytics.schemas import ChartConfigPayload
from redisdb.cache import (
    CacheLayer,
    analytics_cache_key,
    analytics_widget_cache_pattern,
    build_cache_layer,
)
import config as cfg

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
    def _cache(cls, analytics_cache: Optional[CacheLayer] = None) -> CacheLayer:
        return analytics_cache or build_cache_layer(
            "ANALYTICS_CACHE_DB", cfg.ANALYTICS_CACHE_TTL
        )

    @classmethod
    async def _invalidate_widget_cache(
        cls,
        instance_uuid: UUID,
        widget_uuid: UUID,
        analytics_cache: Optional[CacheLayer] = None,
    ) -> None:
        cache = cls._cache(analytics_cache)
        await cache.delete_pattern(
            analytics_widget_cache_pattern(instance_uuid, widget_uuid)
        )

    @classmethod
    async def create_widget(
        cls,
        instance_uuid: UUID,
        payload: WidgetCreateRequest,
        db: AsyncSession,
        analytics_cache: Optional[CacheLayer] = None,
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
        await cls._invalidate_widget_cache(instance_uuid, widget.id, analytics_cache)
        return widget

    @classmethod
    async def get_widget_data(
        cls,
        widget_uuid: UUID,
        instance_uuid: UUID,
        db: AsyncSession,
        mongo_db: Any,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_field: Optional[str] = None,
        analytics_cache: Optional[CacheLayer] = None,
    ) -> List[Dict[str, Any]]:
        cache = cls._cache(analytics_cache)
        cache_key = analytics_cache_key(
            instance_uuid=instance_uuid,
            widget_uuid=widget_uuid,
            date_from=date_from,
            date_to=date_to,
            date_field=date_field,
        )
        cached_data = await cache.get_json(cache_key)
        if cached_data is not None:
            return cached_data

        # 1. Достаем метаданные графика из Postgres
        widget = await AnalyticsWidgetRepository(db).get(instance_uuid, widget_uuid)
        if not widget:
            cls._raise_widget_not_found(widget_uuid, instance_uuid)

        # 2. Инициализируем аналитический слой MongoDB
        analytics_repo = AnalyticsRepository(mongo_db)

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
            date_from=date_from,
            date_to=date_to,
            date_field=date_field,
        )

        await cache.set_json(cache_key, data)
        return data

    @classmethod
    async def update_widget(
        cls,
        widget_uuid: UUID,
        instance_uuid: UUID,
        payload: WidgetUpdateRequest,
        db: AsyncSession,
        analytics_cache: Optional[CacheLayer] = None,
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
        await cls._invalidate_widget_cache(instance_uuid, widget_uuid, analytics_cache)
        return widget

    @classmethod
    async def delete_widget(
        cls,
        widget_uuid: UUID,
        instance_uuid: UUID,
        db: AsyncSession,
        analytics_cache: Optional[CacheLayer] = None,
    ) -> None:
        widget = await AnalyticsWidgetRepository(db).get(instance_uuid, widget_uuid)
        if not widget:
            cls._raise_widget_not_found(widget_uuid, instance_uuid)

        await AnalyticsWidgetRepository(db).delete(widget)
        await db.commit()
        await cls._invalidate_widget_cache(instance_uuid, widget_uuid, analytics_cache)

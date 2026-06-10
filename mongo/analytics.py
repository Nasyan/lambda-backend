# mongo/analytics.py

from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from analytics.schemas import ChartConfigPayload
from mongo.tools.utils import with_active_filter
from logs.mongo import (
    execute_logged_mongo_call,
    log_mongo_query,
    start_mongo_timer,
)


class AnalyticsRepository:
    """
    Специализированный репозиторий для выполнения тяжелых аналитических пайплайнов (BI).
    Изолирован от CRUD-операций RecordRepository.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db["records"]
        # 🔥 Добавляем ссылку на коллекцию шаблонов, чтобы читать схемы полей
        self.templates_collection = db["templates"]

    async def get_schema_definition(self, template_uuid: str) -> Dict[str, Any]:
        """
        Достает чистую схему полей (columns metadata) для конкретного шаблона.
        Если шаблон не найден, возвращает пустой словарь для обратной совместимости.
        """
        query = with_active_filter({"_id": template_uuid})
        template = await execute_logged_mongo_call(
            self.templates_collection,
            "find_one",
            query,
            lambda: self.templates_collection.find_one(query),
            lambda result: 1 if result else 0,
        )
        if not template:
            # Попробуем поискать по строковому uuid или полю uuid, если у вас кастомный id
            fallback_query = with_active_filter({"uuid": template_uuid})
            template = await execute_logged_mongo_call(
                self.templates_collection,
                "find_one",
                fallback_query,
                lambda: self.templates_collection.find_one(fallback_query),
                lambda result: 1 if result else 0,
            )

        if template and "schema" in template:
            return template["schema"]

        return {}

    async def get_chart_data(
        self,
        instance_uuid: str,
        template_uuid: str,
        config: ChartConfigPayload,
        schema_definition: Dict[str, Any],  # 🔥 Теперь схема обязательна
        ast_filter: Optional[Dict[str, Any]] = None,
        limit_data_points: int = 2000,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_field: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Компилирует AST фильтры в Mongo Pipeline и выполняет агрегацию на стороне БД.
        """
        from analytics.builder import MongoPipelineBuilder

        # Инициализируем компилятор и передаем ему схему
        builder = MongoPipelineBuilder(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            schema_definition=schema_definition,  # 🔥 Передаем схему в компилятор
        )

        pipeline = builder.compile_chart(
            config=config,
            ast_filter=ast_filter,
            date_from=date_from,
            date_to=date_to,
            date_field=date_field,
        )
        pipeline.append({"$limit": limit_data_points})

        start_time = start_mongo_timer()
        cursor = self.collection.aggregate(pipeline)
        records = await cursor.to_list(length=limit_data_points)
        log_mongo_query(
            self.collection,
            "aggregate",
            pipeline,
            start_time,
            len(records),
            extra={"limit": limit_data_points},
        )
        return records

# mongo/analytics.py

from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from analytics.schemas import ChartConfigPayload


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
        template = await self.templates_collection.find_one({"_id": template_uuid})
        if not template:
            # Попробуем поискать по строковому uuid или полю uuid, если у вас кастомный id
            template = await self.templates_collection.find_one({"uuid": template_uuid})

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

        pipeline = builder.compile_chart(config=config, ast_filter=ast_filter)
        pipeline.append({"$limit": limit_data_points})

        cursor = self.collection.aggregate(pipeline)
        return await cursor.to_list(length=limit_data_points)

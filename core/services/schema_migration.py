# core/services/schema_migration.py

"""Миграция существующих данных под новую схему (task3, ГЗ-1 Блок B).

Вынесена из RecordRepository.validate_existing_records_against_field:
концептуально это миграция старых записей под новые правила колонки
(retroactive validation + коэрсия значений), а не I/O. Репозиторий
предоставляет только "глупые" примитивы: поток записей и точечный $set.
"""

from typing import Any, Dict, Optional

from mongo.tools.validators import validate_record_data
from mongo.exceptions.record import RecordValidationError


class SchemaMigrationService:
    def __init__(self, record_repo: Any):
        self.record_repo = record_repo

    async def validate_existing_records_against_field(
        self,
        instance_uuid: str,
        template_uuid: str,
        column_name: str,
        new_field_meta: Dict[str, Any],
        s3_service: Optional[Any] = None,
    ) -> None:
        """Проверяет все записи таблицы на совместимость с новыми правилами колонки.

        Если стратегия типа коэрсит значение — записывает новое значение в БД
        (ленивая миграция данных под новую схему). Несовместимая запись
        останавливает процесс доменной ошибкой.
        """
        target_schema = {column_name: new_field_meta}
        is_required = new_field_meta.get("required", False)

        async for record in self.record_repo.stream_records(
            instance_uuid=instance_uuid, template_uuid=template_uuid
        ):
            record_id = record.get("_id")
            record_data = record.get("data", {})

            if is_required and column_name not in record_data:
                raise RecordValidationError(
                    message=f"Существующая запись с ID '{record_id}' не содержит обязательного поля '{column_name}'.",
                    field_name=column_name,
                    reason="retroactive_required_constraint_failed",
                )

            if column_name in record_data:
                isolated_data = {column_name: record_data[column_name]}

                try:
                    await validate_record_data(
                        isolated_data,
                        target_schema,
                        instance_uuid=instance_uuid,
                        record_repo=self.record_repo,
                        s3_service=s3_service,
                    )
                except Exception as e:
                    raise RecordValidationError(
                        message=f"Ошибка обратной совместимости в записи '{record_id}': {str(e)}",
                        field_name=column_name,
                        reason="retroactive_type_migration_failed",
                    )

                if isolated_data[column_name] != record_data[column_name]:
                    await self.record_repo.set_record_data_field(
                        record_id=record_id,
                        column_name=column_name,
                        value=isolated_data[column_name],
                    )

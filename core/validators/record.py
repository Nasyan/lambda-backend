# core/validators/record.py

"""Доменные валидаторы записей (task3, ГЗ-1 Блок B).

Вынесены из RecordRepository: репозиторий остаётся "глупым" I/O-слоем,
а проверка данных по схеме и бизнес-правило уникальности живут здесь
и вызываются из RecordService.

- RecordDataValidator — инфраструктурный валидатор: прогоняет данные через
  стратегии типов (часть стратегий ходит в Mongo за relation/file проверками,
  поэтому ему нужны record_repo и s3_service).
- RecordUniqueConstraintChecker — инфраструктурный чекер бизнес-уникальности
  (читает БД через "глупый" метод репозитория, решение принимает сам).
"""

from typing import Dict, Any, Optional

from mongo.tools.validators import validate_record_data
from mongo.tools.utils import validate_dict_keys
from mongo.exceptions.record import (
    RecordValidationError,
    DuplicateRecordKeyError,
)


class RecordDataValidator:
    """Проверяет (и коэрсит) пользовательские данные по динамической схеме шаблона."""

    def __init__(self, record_repo: Any):
        self.record_repo = record_repo

    async def validate(
        self,
        data: Dict[str, Any],
        schema: Dict[str, Any],
        instance_uuid: str,
        s3_service: Optional[Any] = None,
    ) -> None:
        """Бросает RecordValidationError при несоответствии схеме.

        ВАЖНО: validate_record_data мутирует data in-place (стратегии типов
        коэрсят значения) — это контрактное поведение, на него опирается
        и SchemaMigrationService.
        """
        validate_dict_keys(data)

        try:
            await validate_record_data(
                data,
                schema,
                instance_uuid=instance_uuid,
                record_repo=self.record_repo,
                s3_service=s3_service,
            )
        except RecordValidationError:
            raise
        except Exception as e:
            raise RecordValidationError(
                message=str(e), reason="schema_validation_error"
            )


class RecordUniqueConstraintChecker:
    """Бизнес-правило уникальности полей, помеченных unique: True в схеме."""

    def __init__(self, record_repo: Any):
        self.record_repo = record_repo

    async def check(
        self,
        instance_uuid: str,
        template_uuid: str,
        data: Dict[str, Any],
        schema: Dict[str, Any],
        exclude_record_uuid: Optional[str] = None,
    ) -> None:
        for field_name, field_meta in schema.items():
            if field_meta.get("unique") is True and field_name in data:
                field_value = data[field_name]

                duplicate_exists = await self.record_repo.has_field_value_duplicate(
                    instance_uuid=instance_uuid,
                    template_uuid=template_uuid,
                    field_name=field_name,
                    value=field_value,
                    exclude_record_uuid=exclude_record_uuid,
                )

                if duplicate_exists:
                    raise DuplicateRecordKeyError(
                        message=f"Ошибка уникальности: Поле '{field_name}' уже содержит значение '{field_value}'.",
                        field_name=field_name,
                        invalid_value=field_value,
                        reason="unique_constraint_violation",
                    )

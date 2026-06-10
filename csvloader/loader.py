# csvloader/loader.py

"""CSVLoader — единая точка CSV-выгрузок и загрузок (задания 1-2, 2026-06-10).

Используется двумя контурами:
- analytics: выгрузка точек графика виджета ([{"label", "value"}, ...]);
- core/records: выгрузка записей шаблона по фильтрам и импорт записей из CSV.

Формат значений: вложенные dict/list сериализуются в JSON-строку при экспорте
и парсятся обратно при импорте. Пустая ячейка при импорте = «поле не задано»
(required-валидация остаётся за RecordService).
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, Iterable, List, Optional

from csvloader.exceptions import CSVImportValidationError

# Служебные поля записи: создаются системой, при импорте игнорируются.
RECORD_SERVICE_FIELDS = (
    "_id",
    "instance_uuid",
    "template_uuid",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
    "version",
    "is_deleted",
)

# Типы полей схемы, которые вычисляются сервером и не импортируются из CSV.
_COMPUTED_FIELD_TYPES = {"formula"}


class CSVLoader:
    """Сериализация табличных данных в CSV и обратно."""

    def __init__(self, delimiter: str = ","):
        self.delimiter = delimiter

    # ------------------------------------------------------------------ export

    def rows_to_csv(
        self,
        rows: Iterable[Dict[str, Any]],
        fieldnames: Optional[List[str]] = None,
    ) -> str:
        """Универсальная выгрузка списка словарей в CSV-строку.

        Если fieldnames не заданы — объединение ключей всех строк в порядке
        первого появления.
        """
        rows = list(rows)
        if fieldnames is None:
            fieldnames = []
            for row in rows:
                for key in row:
                    if key not in fieldnames:
                        fieldnames.append(key)

        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=fieldnames,
            delimiter=self.delimiter,
            extrasaction="ignore",
            restval="",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: self._serialize_value(row.get(key)) for key in fieldnames})
        return buffer.getvalue()

    def analytics_to_csv(self, data_points: Iterable[Dict[str, Any]]) -> str:
        """Выгрузка точек аналитики виджета (label/value)."""
        return self.rows_to_csv(data_points, fieldnames=["label", "value"])

    def records_to_csv(
        self,
        records: Iterable[Dict[str, Any]],
        schema: Dict[str, Any],
        include_service_fields: bool = True,
    ) -> str:
        """Выгрузка записей шаблона: колонки = служебные поля + поля схемы.

        Каждое поле data выносится в собственную колонку; то, чего нет в схеме,
        попадает в колонки только если присутствует в data (хвостом, после
        схемных колонок).
        """
        records = list(records)
        schema_fields = list(schema.keys())
        extra_fields: List[str] = []
        flat_rows: List[Dict[str, Any]] = []

        for record in records:
            data = record.get("data") or {}
            flat: Dict[str, Any] = {}
            if include_service_fields:
                for service_field in RECORD_SERVICE_FIELDS:
                    if service_field in record:
                        flat[service_field] = record[service_field]
            for field_name, value in data.items():
                flat[field_name] = value
                if field_name not in schema_fields and field_name not in extra_fields:
                    extra_fields.append(field_name)
            flat_rows.append(flat)

        fieldnames: List[str] = []
        if include_service_fields:
            present_service = {
                key for row in flat_rows for key in row if key in RECORD_SERVICE_FIELDS
            }
            fieldnames.extend(
                [field for field in RECORD_SERVICE_FIELDS if field in present_service]
            )
        fieldnames.extend(schema_fields)
        fieldnames.extend(extra_fields)
        return self.rows_to_csv(flat_rows, fieldnames=fieldnames)

    # ------------------------------------------------------------------ import

    def csv_to_record_payloads(
        self,
        content: str | bytes,
        schema: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Парсит CSV в список data-payload'ов для создания записей.

        Берутся ТОЛЬКО поля из схемы шаблона (служебные колонки и неизвестные
        заголовки игнорируются — служебные поля создаёт система). Formula-поля
        не импортируются (вычисляются сервером). Значения приводятся к типу
        поля схемы; ошибки приведения собираются по всем строкам и кидаются
        одним CSVImportValidationError (построчный отчёт, ничего не создано).
        """
        if isinstance(content, bytes):
            content = content.decode("utf-8-sig")
        else:
            content = content.lstrip("﻿")

        reader = csv.DictReader(io.StringIO(content), delimiter=self.delimiter)
        if not reader.fieldnames:
            raise CSVImportValidationError(
                [{"row": 0, "field": None, "detail": "CSV пустой или без заголовка"}]
            )

        importable_fields = {
            name: meta
            for name, meta in schema.items()
            if (meta or {}).get("type") not in _COMPUTED_FIELD_TYPES
        }

        payloads: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        for row_idx, raw_row in enumerate(reader, start=1):
            data: Dict[str, Any] = {}
            for field_name, field_meta in importable_fields.items():
                raw_value = raw_row.get(field_name)
                if raw_value is None or raw_value == "":
                    continue  # незаданное поле; required проверит RecordService
                try:
                    data[field_name] = self._coerce_value(raw_value, field_meta or {})
                except ValueError as exc:
                    errors.append(
                        {"row": row_idx, "field": field_name, "detail": str(exc)}
                    )
            payloads.append(data)

        if errors:
            raise CSVImportValidationError(errors)
        if not payloads:
            raise CSVImportValidationError(
                [{"row": 0, "field": None, "detail": "CSV не содержит строк данных"}]
            )
        return payloads

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return "true" if value else "false"
        return value

    @staticmethod
    def _coerce_value(raw_value: str, field_meta: Dict[str, Any]) -> Any:
        field_type = field_meta.get("type", "string")

        if field_type == "number":
            normalized = raw_value.strip().replace(",", ".")
            try:
                number = float(normalized)
            except ValueError as exc:
                raise ValueError(f"не число: {raw_value!r}") from exc
            return int(number) if number.is_integer() else number

        if field_type == "boolean":
            lowered = raw_value.strip().lower()
            if lowered in {"true", "1", "yes", "да"}:
                return True
            if lowered in {"false", "0", "no", "нет"}:
                return False
            raise ValueError(f"не boolean: {raw_value!r}")

        if field_type in {"relation_list", "cascading_tree"}:
            try:
                return json.loads(raw_value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"ожидался JSON для {field_type}: {raw_value!r}") from exc

        # string / date / select / email / phone / relation — строка как есть
        return raw_value

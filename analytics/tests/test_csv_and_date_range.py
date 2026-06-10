# analytics/tests/test_csv_and_date_range.py

"""Задание 1 (2026-06-10): диапазон дат в аналитике + выгрузка CSV виджета."""

import csv
import io
import uuid

import pytest

from analytics.builder import MongoPipelineBuilder
from analytics.exceptions import InvalidAggregationConfigError
from analytics.schemas import ChartConfigPayload


def _datetime_config(date_bucket: str = "day") -> ChartConfigPayload:
    return ChartConfigPayload(
        axis_x={"field": "purchased_at", "type": "datetime", "date_bucket": date_bucket},
        axis_y={"field": "_id", "aggregation": "COUNT"},
    )


class TestDateRangePipelineUnit:
    """Юнит-уровень: корректность стадий пайплайна для date_from/date_to и бакетов."""

    def test_date_range_adds_match_stage_right_after_base_match(self):
        builder = MongoPipelineBuilder(str(uuid.uuid4()), str(uuid.uuid4()))
        pipeline = builder.compile_chart(
            _datetime_config(), date_from="2026-06-01", date_to="2026-06-07"
        )
        assert pipeline[1] == {
            "$match": {"data.purchased_at": {"$gte": "2026-06-01", "$lte": "2026-06-07"}}
        }

    def test_open_ended_range_only_from(self):
        builder = MongoPipelineBuilder(str(uuid.uuid4()), str(uuid.uuid4()))
        pipeline = builder.compile_chart(_datetime_config(), date_from="2026-06-01")
        assert pipeline[1] == {"$match": {"data.purchased_at": {"$gte": "2026-06-01"}}}

    def test_explicit_date_field_works_for_categorical_axis(self):
        # Pie/категориальная ось: диапазон режется по явному date_field
        builder = MongoPipelineBuilder(str(uuid.uuid4()), str(uuid.uuid4()))
        config = ChartConfigPayload(
            axis_x={"field": "category", "type": "categorical"},
            axis_y={"field": "_id", "aggregation": "COUNT"},
        )
        pipeline = builder.compile_chart(
            config, date_from="2026-06-01", date_field="purchased_at"
        )
        assert pipeline[1] == {"$match": {"data.purchased_at": {"$gte": "2026-06-01"}}}

    def test_range_without_datetime_axis_and_without_date_field_fails(self):
        builder = MongoPipelineBuilder(str(uuid.uuid4()), str(uuid.uuid4()))
        config = ChartConfigPayload(
            axis_x={"field": "category", "type": "categorical"},
            axis_y={"field": "_id", "aggregation": "COUNT"},
        )
        with pytest.raises(InvalidAggregationConfigError):
            builder.compile_chart(config, date_from="2026-06-01")

    def test_no_range_means_no_extra_match_stage(self):
        builder = MongoPipelineBuilder(str(uuid.uuid4()), str(uuid.uuid4()))
        pipeline = builder.compile_chart(_datetime_config())
        # $match базовый -> $group -> $project -> $sort
        assert len(pipeline) == 4

    @pytest.mark.parametrize(
        "bucket,expected_format",
        [
            ("hour", "%Y-%m-%d %H:00"),
            ("hour_of_day", "%H"),
            ("weekday", "%u"),
            ("day", "%Y-%m-%d"),
        ],
    )
    def test_datetime_buckets_compile_to_expected_formats(self, bucket, expected_format):
        builder = MongoPipelineBuilder(str(uuid.uuid4()), str(uuid.uuid4()))
        pipeline = builder.compile_chart(_datetime_config(bucket))
        group_stage = next(stage for stage in pipeline if "$group" in stage)
        date_to_string = group_stage["$group"]["_id"]["$dateToString"]
        assert date_to_string["format"] == expected_format
        # Строковые ISO-даты конвертируются в BSON-дату перед форматированием
        assert date_to_string["date"]["$convert"]["input"] == "$data.purchased_at"


PURCHASES_SCHEMA = {
    "amount": {"type": "number", "required": True},
    "purchased_at": {"type": "datetime", "required": True},
    "category": {"type": "string", "required": False},
}


async def _bootstrap_purchases(test_client, instance_uuid, headers, purchases):
    """Создаёт шаблон «Покупки» и записи [(amount, purchased_at, category), ...]."""
    tpl = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        headers=headers,
        json={"name": f"Покупки {uuid.uuid4().hex[:6]}", "schema": PURCHASES_SCHEMA},
    )
    assert tpl.status_code == 201, tpl.text
    tpl_id = tpl.json()["_id"]
    notes_url = f"/instances/{instance_uuid}/templates/{tpl_id}/notes"
    for amount, purchased_at, category in purchases:
        resp = await test_client.post(
            notes_url,
            headers=headers,
            json={
                "data": {
                    "amount": amount,
                    "purchased_at": purchased_at,
                    "category": category,
                }
            },
        )
        assert resp.status_code == 201, resp.text
    return tpl_id


async def _create_widget(test_client, instance_uuid, headers, tpl_id, widget_type, chart_config):
    resp = await test_client.post(
        f"/instances/{instance_uuid}/widgets",
        headers=headers,
        json={
            "name": f"widget-{uuid.uuid4().hex[:6]}",
            "target_template_uuid": tpl_id,
            "widget_type": widget_type,
            "chart_config": chart_config,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _labels(data_points):
    return {point["label"]: point["value"] for point in data_points}


class TestWidgetDateRangeE2E:

    @pytest.mark.asyncio
    async def test_purchases_per_hour_of_day_with_date_range(
        self, test_client, create_test_environment
    ):
        """Диаграмма «в какое время суток больше покупают» + диапазон дат."""
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_purchases(
            test_client,
            instance_uuid,
            headers,
            [
                # внутри диапазона: три покупки в 10ч, одна в 14ч
                (100, "2026-06-01T10:05:00", "еда"),
                (150, "2026-06-01T10:25:00", "еда"),
                (90, "2026-06-01T10:45:00", "техника"),
                (200, "2026-06-01T14:10:00", "еда"),
                # вне диапазона (май) — не должны попасть
                (500, "2026-05-20T10:15:00", "еда"),
                (500, "2026-05-20T10:35:00", "еда"),
            ],
        )
        widget_id = await _create_widget(
            test_client,
            instance_uuid,
            headers,
            tpl_id,
            "BAR",
            {
                "axis_x": {
                    "field": "purchased_at",
                    "type": "datetime",
                    "date_bucket": "hour_of_day",
                },
                "axis_y": {"field": "_id", "aggregation": "COUNT"},
            },
        )

        ranged = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{widget_id}/data",
            headers=headers,
            params={"date_from": "2026-06-01", "date_to": "2026-06-02"},
        )
        assert ranged.status_code == 200, ranged.text
        assert _labels(ranged.json()) == {"10": 3, "14": 1}

        # Без диапазона майские покупки возвращаются (10ч = 5 покупок)
        full = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{widget_id}/data", headers=headers
        )
        assert _labels(full.json()) == {"10": 5, "14": 1}

    @pytest.mark.asyncio
    async def test_weekday_pattern_average_over_two_weeks(
        self, test_client, create_test_environment
    ):
        """«В среднем в будние покупают больше»: weekday-бакет по двум неделям.

        Будни: по 2 покупки/день каждую неделю; выходные: по 1. На диапазоне
        двух недель count по будним дням (4) стабильно выше выходных (2);
        среднее за неделю = value / 2 недели — пропорция та же.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()
        purchases = []
        # Недели 2026-06-01..07 и 2026-06-08..14 (1 июня 2026 — понедельник)
        for monday in ("2026-06-01", "2026-06-08"):
            base_day = int(monday[-2:])
            for offset in range(5):  # будни ×2
                day = f"2026-06-{base_day + offset:02d}"
                purchases.append((100, f"{day}T12:00:00", "еда"))
                purchases.append((120, f"{day}T18:30:00", "еда"))
            for offset in (5, 6):  # выходные ×1
                day = f"2026-06-{base_day + offset:02d}"
                purchases.append((80, f"{day}T13:00:00", "еда"))

        tpl_id = await _bootstrap_purchases(test_client, instance_uuid, headers, purchases)
        widget_id = await _create_widget(
            test_client,
            instance_uuid,
            headers,
            tpl_id,
            "BAR",
            {
                "axis_x": {
                    "field": "purchased_at",
                    "type": "datetime",
                    "date_bucket": "weekday",
                },
                "axis_y": {"field": "_id", "aggregation": "COUNT"},
            },
        )

        resp = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{widget_id}/data",
            headers=headers,
            params={"date_from": "2026-06-01", "date_to": "2026-06-15"},
        )
        assert resp.status_code == 200, resp.text
        by_weekday = _labels(resp.json())

        weekdays = [by_weekday[str(day)] for day in range(1, 6)]  # Пн..Пт
        weekends = [by_weekday[str(day)] for day in (6, 7)]  # Сб, Вс
        assert weekdays == [4, 4, 4, 4, 4]
        assert weekends == [2, 2]
        assert min(weekdays) > max(weekends)

    @pytest.mark.asyncio
    async def test_pie_widget_by_category_with_explicit_date_field(
        self, test_client, create_test_environment
    ):
        """Пирог по категориям + диапазон дат через явный date_field."""
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_purchases(
            test_client,
            instance_uuid,
            headers,
            [
                (100, "2026-06-01T10:00:00", "еда"),
                (150, "2026-06-01T11:00:00", "еда"),
                (90, "2026-06-01T12:00:00", "техника"),
                (500, "2026-05-20T10:00:00", "еда"),  # вне диапазона
            ],
        )
        widget_id = await _create_widget(
            test_client,
            instance_uuid,
            headers,
            tpl_id,
            "PIE",
            {
                "axis_x": {"field": "category", "type": "categorical"},
                "axis_y": {"field": "_id", "aggregation": "COUNT"},
            },
        )

        resp = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{widget_id}/data",
            headers=headers,
            params={
                "date_from": "2026-06-01",
                "date_to": "2026-06-02",
                "date_field": "purchased_at",
            },
        )
        assert resp.status_code == 200, resp.text
        assert _labels(resp.json()) == {"еда": 2, "техника": 1}

    @pytest.mark.asyncio
    async def test_line_widget_per_day_and_csv_export(
        self, test_client, create_test_environment
    ):
        """Линия по дням + выгрузка тех же данных в CSV с диапазоном."""
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_purchases(
            test_client,
            instance_uuid,
            headers,
            [
                (100, "2026-06-01T10:00:00", "еда"),
                (150, "2026-06-01T16:00:00", "еда"),
                (90, "2026-06-02T12:00:00", "еда"),
                (70, "2026-05-31T12:00:00", "еда"),  # вне диапазона
            ],
        )
        widget_id = await _create_widget(
            test_client,
            instance_uuid,
            headers,
            tpl_id,
            "LINE",
            {
                "axis_x": {
                    "field": "purchased_at",
                    "type": "datetime",
                    "date_bucket": "day",
                },
                "axis_y": {"field": "amount", "aggregation": "SUM"},
            },
        )

        params = {"date_from": "2026-06-01", "date_to": "2026-06-03"}
        data_resp = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{widget_id}/data",
            headers=headers,
            params=params,
        )
        assert _labels(data_resp.json()) == {"2026-06-01": 250, "2026-06-02": 90}

        csv_resp = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{widget_id}/export-csv",
            headers=headers,
            params=params,
        )
        assert csv_resp.status_code == 200, csv_resp.text
        assert csv_resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in csv_resp.headers["content-disposition"]

        rows = list(csv.DictReader(io.StringIO(csv_resp.text)))
        assert {row["label"]: float(row["value"]) for row in rows} == {
            "2026-06-01": 250.0,
            "2026-06-02": 90.0,
        }

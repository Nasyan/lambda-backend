# core/tests/test_csv_records.py

"""Задание 2 (2026-06-10): выгрузка записей шаблона в CSV по фильтрам и
импорт записей из CSV через единый CSVLoader."""

import csv
import io
import uuid

import pytest

CLIENTS_SCHEMA = {
    "name": {"type": "string", "required": True},
    "age": {"type": "number", "required": False},
}


async def _bootstrap_template(test_client, instance_uuid, headers, schema=None):
    tpl = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        headers=headers,
        json={
            "name": f"Клиенты {uuid.uuid4().hex[:6]}",
            "schema": schema or CLIENTS_SCHEMA,
        },
    )
    assert tpl.status_code == 201, tpl.text
    return tpl.json()["_id"]


async def _create_record(test_client, instance_uuid, tpl_id, headers, data):
    resp = await test_client.post(
        f"/instances/{instance_uuid}/templates/{tpl_id}/notes",
        headers=headers,
        json={"data": data},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _parse_csv(text):
    return list(csv.DictReader(io.StringIO(text)))


class TestRecordsCSVExport:

    @pytest.mark.asyncio
    async def test_export_records_csv_with_filters(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_template(test_client, instance_uuid, headers)
        for name, age in (("Иван", 30), ("Оля", 40), ("Юниор", 20)):
            await _create_record(
                test_client, instance_uuid, tpl_id, headers, {"name": name, "age": age}
            )

        resp = await test_client.get(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes/export-csv",
            headers=headers,
            params={"filters": '{"age": {"$gt": 25}}', "sort_by": "age"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]

        rows = _parse_csv(resp.text)
        assert [row["name"] for row in rows] == ["Иван", "Оля"]
        # number-валидатор хранит числа как float → в CSV "30.0"/"40.0"
        assert [float(row["age"]) for row in rows] == [30.0, 40.0]
        # Служебные поля выгружаются вместе с данными
        for row in rows:
            assert row["_id"]
            assert row["created_by"] == str(user_uuid)
            assert row["version"] == "1"

    @pytest.mark.asyncio
    async def test_export_csv_unknown_template_404(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()
        resp = await test_client.get(
            f"/instances/{instance_uuid}/templates/{uuid.uuid4()}/notes/export-csv",
            headers=headers,
        )
        assert resp.status_code == 404


class TestRecordsCSVImport:

    @pytest.mark.asyncio
    async def test_import_csv_creates_records_with_service_fields(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_template(test_client, instance_uuid, headers)

        resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes/import-csv",
            headers=headers,
            json={"csv_content": "name,age\nИван,30\nОля,25\n"},
        )
        assert resp.status_code == 201, resp.text
        report = resp.json()
        assert report["created"] == 2
        assert report["failed"] == 0
        assert len(report["created_ids"]) == 2

        listing = await test_client.get(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes",
            headers=headers,
            params={"sort_by": "age", "descending": True},
        )
        records = listing.json()["results"]
        assert listing.json()["total"] == 2
        assert records[0]["data"] == {"name": "Иван", "age": 30}  # number приведён
        # Служебные поля созданы системой
        assert records[0]["_id"] in report["created_ids"]
        assert records[0]["created_by"] == str(user_uuid)
        assert records[0]["version"] == 1

    @pytest.mark.asyncio
    async def test_import_csv_type_error_aborts_whole_import(
        self, test_client, create_test_environment
    ):
        """Ошибка приведения типов в любой строке — 422 и ничего не создано."""
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_template(test_client, instance_uuid, headers)

        resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes/import-csv",
            headers=headers,
            json={"csv_content": "name,age\nИван,30\nОля,тридцать\n"},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["errors"][0]["row"] == 2
        assert detail["errors"][0]["field"] == "age"

        listing = await test_client.get(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes", headers=headers
        )
        assert listing.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_import_csv_required_violation_reported_per_row(
        self, test_client, create_test_environment
    ):
        """Пустая required-ячейка валится доменной валидацией: строка в отчёте
        об ошибках, остальные строки созданы."""
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_template(test_client, instance_uuid, headers)

        resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes/import-csv",
            headers=headers,
            json={"csv_content": "name,age\n,30\nОля,25\n"},
        )
        assert resp.status_code == 201, resp.text
        report = resp.json()
        assert report["created"] == 1
        assert report["failed"] == 1
        assert report["errors"][0]["row"] == 1

        listing = await test_client.get(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes", headers=headers
        )
        assert listing.json()["total"] == 1
        assert listing.json()["results"][0]["data"]["name"] == "Оля"

    @pytest.mark.asyncio
    async def test_import_csv_ignores_service_and_unknown_columns(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_template(test_client, instance_uuid, headers)

        csv_content = (
            "_id,name,age,version,unknown_column\n" f"{uuid.uuid4()},Иван,30,99,мусор\n"
        )
        resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes/import-csv",
            headers=headers,
            json={"csv_content": csv_content},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["created"] == 1

        listing = await test_client.get(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes", headers=headers
        )
        record = listing.json()["results"][0]
        # _id/version выданы системой, мусорная колонка не попала в data
        assert record["_id"] not in csv_content.splitlines()[1]
        assert record["version"] == 1
        assert record["data"] == {"name": "Иван", "age": 30}

    @pytest.mark.asyncio
    async def test_export_import_roundtrip(self, test_client, create_test_environment):
        """Выгрузка CSV из одного шаблона и загрузка в другой с той же схемой."""
        user_uuid, instance_uuid, headers = await create_test_environment()
        source_tpl = await _bootstrap_template(test_client, instance_uuid, headers)
        target_tpl = await _bootstrap_template(test_client, instance_uuid, headers)

        for name, age in (("Иван", 30), ("Оля", 40)):
            await _create_record(
                test_client,
                instance_uuid,
                source_tpl,
                headers,
                {"name": name, "age": age},
            )

        export_resp = await test_client.get(
            f"/instances/{instance_uuid}/templates/{source_tpl}/notes/export-csv",
            headers=headers,
        )
        assert export_resp.status_code == 200

        import_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{target_tpl}/notes/import-csv",
            headers=headers,
            json={"csv_content": export_resp.text},
        )
        assert import_resp.status_code == 201, import_resp.text
        assert import_resp.json()["created"] == 2

        listing = await test_client.get(
            f"/instances/{instance_uuid}/templates/{target_tpl}/notes",
            headers=headers,
            params={"sort_by": "age"},
        )
        assert listing.json()["total"] == 2
        assert [r["data"] for r in listing.json()["results"]] == [
            {"name": "Иван", "age": 30},
            {"name": "Оля", "age": 40},
        ]

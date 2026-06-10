# core/tests/test_history_versioning.py

"""Задание 3 (2026-06-10): аудит history + версионирования.

Проверяет, что КАЖДЫЙ мутационный путь записи (create / update / delete /
restore / trigger-DML) инкрементирует version и пишет append-only снимок
в records_history, а history-эндпоинты возвращают корректную хронологию.
"""

import pytest

CLIENTS_SCHEMA = {
    "name": {"type": "string", "required": True},
    "status": {"type": "string", "required": False},
}


async def _bootstrap_template(test_client, instance_uuid, headers, name="Журнал"):
    resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        headers=headers,
        json={"name": name, "schema": CLIENTS_SCHEMA},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["_id"]


class TestRecordLifecycleHistory:

    @pytest.mark.asyncio
    async def test_create_and_updates_write_versioned_snapshots(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_template(test_client, instance_uuid, headers)
        notes_url = f"/instances/{instance_uuid}/templates/{tpl_id}/notes"

        created = await test_client.post(
            notes_url, headers=headers, json={"data": {"name": "Отчёт", "status": "draft"}}
        )
        record_uuid = created.json()["_id"]
        assert created.json()["version"] == 1

        patch1 = await test_client.patch(
            f"{notes_url}/{record_uuid}", headers=headers,
            json={"data": {"status": "review"}},
        )
        assert patch1.json()["version"] == 2

        patch2 = await test_client.patch(
            f"{notes_url}/{record_uuid}", headers=headers,
            json={"data": {"status": "published"}},
        )
        assert patch2.json()["version"] == 3

        history = await test_client.get(
            f"/history/record/{record_uuid}/", headers=headers
        )
        assert history.status_code == 200, history.text
        items = history.json()["history"]
        assert [item["version"] for item in items] == [3, 2, 1]
        assert items[0]["snapshot"]["data"]["status"] == "published"
        assert items[1]["snapshot"]["data"]["status"] == "review"
        assert items[2]["snapshot"]["data"]["status"] == "draft"
        # Авторство: все изменения сделал создатель
        assert {item["user_uuid"] for item in items} == {str(user_uuid)}

    @pytest.mark.asyncio
    async def test_field_history_endpoint_tracks_value_per_version(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_template(test_client, instance_uuid, headers)
        notes_url = f"/instances/{instance_uuid}/templates/{tpl_id}/notes"

        created = await test_client.post(
            notes_url, headers=headers, json={"data": {"name": "Док", "status": "a"}}
        )
        record_uuid = created.json()["_id"]
        await test_client.patch(
            f"{notes_url}/{record_uuid}", headers=headers, json={"data": {"status": "b"}}
        )

        resp = await test_client.get(
            f"/history/field/{record_uuid}/status/", headers=headers
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()["history"]
        values = [(item["version"], item["value"]) for item in items]
        assert values == [(2, "b"), (1, "a")]

    @pytest.mark.asyncio
    async def test_delete_and_restore_bump_version_and_log_markers(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()
        tpl_id = await _bootstrap_template(test_client, instance_uuid, headers)
        notes_url = f"/instances/{instance_uuid}/templates/{tpl_id}/notes"

        created = await test_client.post(
            notes_url, headers=headers, json={"data": {"name": "Времянка", "status": "x"}}
        )
        record_uuid = created.json()["_id"]
        await test_client.patch(
            f"{notes_url}/{record_uuid}", headers=headers, json={"data": {"status": "y"}}
        )  # v2

        delete_resp = await test_client.delete(
            f"{notes_url}/{record_uuid}", headers=headers
        )
        assert delete_resp.status_code == 204, delete_resp.text

        # Удалённая запись получила version=3 и updated_by
        deleted_list = await test_client.get(f"{notes_url}/deleted", headers=headers)
        deleted = deleted_list.json()["results"][0]
        assert deleted["_id"] == record_uuid
        assert deleted["version"] == 3
        assert deleted["updated_by"] == str(user_uuid)

        # Пока запись удалена: старые снимки скрыты, виден только маркер удаления
        history_deleted = await test_client.get(
            f"/history/record/{record_uuid}/", headers=headers
        )
        items = history_deleted.json()["history"]
        assert [item["version"] for item in items] == [3]
        assert items[0]["snapshot"]["is_deleted"] is True

        restore_resp = await test_client.post(
            f"{notes_url}/{record_uuid}/restore", headers=headers
        )
        assert restore_resp.status_code == 200, restore_resp.text
        assert restore_resp.json()["version"] == 4

        # После восстановления видна ПОЛНАЯ хронология: v1, v2, маркер удаления v3,
        # маркер восстановления v4
        history_full = await test_client.get(
            f"/history/record/{record_uuid}/", headers=headers
        )
        items = history_full.json()["history"]
        assert [item["version"] for item in items] == [4, 3, 2, 1]
        assert items[0]["snapshot"]["is_deleted"] is False
        assert items[1]["snapshot"]["is_deleted"] is True


class TestAutomationDMLHistory:

    @pytest.mark.asyncio
    async def test_trigger_upsert_writes_history_as_system_automation(
        self, test_client, create_test_environment
    ):
        """Цепочка: создание заказа → автоматизация UPSERT'ит клиента.
        Каждая системная мутация клиента обязана попасть в history."""
        user_uuid, instance_uuid, headers = await create_test_environment()
        base = f"/instances/{instance_uuid}"

        orders = await test_client.post(
            f"{base}/templates",
            headers=headers,
            json={
                "name": "Заказы",
                "schema": {
                    "client_phone": {"type": "string", "required": True},
                    "client_name": {"type": "string", "required": True},
                },
            },
        )
        orders_id = orders.json()["_id"]
        clients = await test_client.post(
            f"{base}/templates",
            headers=headers,
            json={
                "name": "Клиенты",
                "schema": {
                    "phone": {"type": "string", "required": True},
                    "name": {"type": "string", "required": False},
                },
            },
        )
        clients_id = clients.json()["_id"]

        trigger_payload = {
            "name": "upsert клиента из заказа",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "source_template_uuid": orders_id,
            "target_template_uuid": clients_id,
            "payload_ast": {
                "type": "object",
                "fields": {
                    "phone": {"type": "field", "value": "client_phone"},
                    "name": {"type": "field", "value": "client_name"},
                },
            },
            "action_name": "UPSERT_RECORD",
            "action_params": {"search_fields": ["phone"]},
            "action_mapping_ast": {
                "type": "object",
                "fields": {
                    "phone": {"type": "field", "value": "client_phone"},
                    "name": {"type": "field", "value": "client_name"},
                },
            },
        }
        trig = await test_client.post(
            f"{base}/triggers/", headers=headers, json=trigger_payload
        )
        assert trig.status_code == 201, trig.text

        phone = "+375291234567"
        for order_name in ("Анна", "Анна Обновлённая"):
            resp = await test_client.post(
                f"{base}/templates/{orders_id}/notes",
                headers=headers,
                json={"data": {"client_phone": phone, "client_name": order_name}},
            )
            assert resp.status_code == 201, resp.text

        clients_list = await test_client.get(
            f"{base}/templates/{clients_id}/notes", headers=headers
        )
        assert clients_list.json()["total"] == 1
        client_record = clients_list.json()["results"][0]
        # Первый заказ — upsert-insert (v1), второй — upsert-update (v2)
        assert client_record["version"] == 2
        assert client_record["data"]["name"] == "Анна Обновлённая"
        assert client_record["updated_by"] == "system_automation"

        history = await test_client.get(
            f"/history/record/{client_record['_id']}/", headers=headers
        )
        assert history.status_code == 200, history.text
        items = history.json()["history"]
        assert [item["version"] for item in items] == [2, 1]
        assert {item["user_uuid"] for item in items} == {"system_automation"}
        assert items[0]["snapshot"]["data"]["name"] == "Анна Обновлённая"
        assert items[1]["snapshot"]["data"]["name"] == "Анна"

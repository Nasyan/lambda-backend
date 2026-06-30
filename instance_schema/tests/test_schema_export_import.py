# instance_schema/tests/test_schema_export_import.py

"""Задание 4 (2026-06-10): выгрузка/загрузка полной схемы инстанса.

Бизнес-сценарий по мотивам playground (программа лояльности):
Заказы → (T1 upsert) → Клиенты → (T2 insert) → Награды, плюс formula-агрегация
orders_count, аналитический виджет, storefront-политика и notification template.
Проверяем: экспорт, скрупулёзную валидацию, топологический порядок, ремап UUID,
работоспособность CRM после импорта (каскады реально стреляют), режимы
merge/replace и откат на previous_schema.
"""

import pytest


def _loyalty_setup_payloads():
    """Конфигурация сценария. UUID шаблонов проставляются после создания."""
    return {
        "orders_schema": {
            "client_phone": {"type": "string", "required": True},
            "client_name": {"type": "string", "required": True},
            "amount": {"type": "number", "required": False},
        },
        "rewards_schema": {
            "phone": {"type": "string", "required": True},
            "reason": {"type": "string", "required": False},
        },
    }


def _clients_schema(orders_uuid: str):
    return {
        "phone": {"type": "string", "required": True},
        "name": {"type": "string", "required": False},
        "orders_count": {
            "type": "formula",
            "required": False,
            "ast": {
                "type": "aggregation",
                "target_template_uuid": orders_uuid,
                "filter_field": "client_phone",
                "filter_value": {"type": "field", "value": "phone"},
                "agg_function": "count",
                "agg_field": None,
            },
        },
    }


async def _build_loyalty_instance(test_client, instance_uuid, headers):
    """Создаёт полный конфиг через публичные API. Возвращает uuid-ы шаблонов."""
    payloads = _loyalty_setup_payloads()
    base = f"/instances/{instance_uuid}"

    orders = await test_client.post(
        f"{base}/templates",
        headers=headers,
        json={"name": "Заказы", "schema": payloads["orders_schema"]},
    )
    assert orders.status_code == 201, orders.text
    orders_id = orders.json()["_id"]

    clients = await test_client.post(
        f"{base}/templates",
        headers=headers,
        json={"name": "Клиенты", "schema": _clients_schema(orders_id)},
    )
    assert clients.status_code == 201, clients.text
    clients_id = clients.json()["_id"]

    rewards = await test_client.post(
        f"{base}/templates",
        headers=headers,
        json={"name": "Награды", "schema": payloads["rewards_schema"]},
    )
    assert rewards.status_code == 201, rewards.text
    rewards_id = rewards.json()["_id"]

    # T1: заказ создан → upsert клиента (CREATE/UPDATE каскад на Клиентов)
    t1 = await test_client.post(
        f"{base}/triggers",
        headers=headers,
        json={
            "name": "T1 upsert клиента",
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
        },
    )
    assert t1.status_code == 201, t1.text

    # T2: клиент обновлён → запись в Награды (зависит от T1: его target = source T2)
    t2 = await test_client.post(
        f"{base}/triggers",
        headers=headers,
        json={
            "name": "T2 награда за повторный заказ",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_UPDATE",
            "source_template_uuid": clients_id,
            "target_template_uuid": rewards_id,
            "payload_ast": {
                "type": "object",
                "fields": {
                    "phone": {"type": "field", "value": "phone"},
                    "reason": {"type": "literal", "value": "повторный заказ"},
                },
            },
            "action_name": "INSERT_RECORD",
            "action_mapping_ast": {
                "type": "object",
                "fields": {
                    "phone": {"type": "field", "value": "phone"},
                    "reason": {"type": "literal", "value": "повторный заказ"},
                },
            },
        },
    )
    assert t2.status_code == 201, t2.text

    widget = await test_client.post(
        f"{base}/widgets",
        headers=headers,
        json={
            "name": "Заказы по клиентам",
            "target_template_uuid": orders_id,
            "widget_type": "BAR",
            "chart_config": {
                "axis_x": {"field": "client_phone", "type": "categorical"},
                "axis_y": {"field": "_id", "aggregation": "COUNT"},
            },
        },
    )
    assert widget.status_code == 201, widget.text

    policy = await test_client.post(
        f"{base}/storefront-configs",
        headers=headers,
        json={
            "template_name": "Заказы",
            "read_filters": {},
            "read_mask": ["client_name", "amount"],
            "write_mask": ["client_phone", "client_name", "amount"],
        },
    )
    assert policy.status_code in (200, 201), policy.text

    notification = await test_client.post(
        f"{base}/notifications/templates",
        headers=headers,
        json={
            "name": "Новый заказ",
            "title": "Заказ от {{data.client_name}}",
            "body": "Телефон: {{data.client_phone}}",
            "channels": ["crm"],
            "recipients_config": {"roles": ["CREATOR"]},
            "source_template_uuid": orders_id,
        },
    )
    assert notification.status_code in (200, 201), notification.text

    return orders_id, clients_id, rewards_id


async def _export(test_client, instance_uuid, headers):
    resp = await test_client.get(
        f"/instances/{instance_uuid}/schema/export", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestExport:

    @pytest.mark.asyncio
    async def test_export_returns_full_bundle(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()
        await _build_loyalty_instance(test_client, instance_uuid, headers)

        bundle = await _export(test_client, instance_uuid, headers)

        assert bundle["format_version"] == 1
        assert {t["name"] for t in bundle["templates"]} == {
            "Заказы",
            "Клиенты",
            "Награды",
        }
        assert {t["name"] for t in bundle["triggers"]} == {
            "T1 upsert клиента",
            "T2 награда за повторный заказ",
        }
        assert [w["name"] for w in bundle["widgets"]] == ["Заказы по клиентам"]
        assert [p["template_name"] for p in bundle["policies"]] == ["Заказы"]
        assert [n["name"] for n in bundle["notification_templates"]] == ["Новый заказ"]
        # Схема шаблона уходит под ключом "schema" (alias)
        clients_cfg = next(t for t in bundle["templates"] if t["name"] == "Клиенты")
        assert "orders_count" in clients_cfg["schema"]


class TestImportValidation:

    @pytest.mark.asyncio
    async def test_dry_run_validates_and_plans_order(
        self, test_client, create_test_environment
    ):
        user_uuid, src_instance, headers = await create_test_environment()
        await _build_loyalty_instance(test_client, src_instance, headers)
        bundle = await _export(test_client, src_instance, headers)

        _, dst_instance, dst_headers = await create_test_environment()
        resp = await test_client.post(
            f"/instances/{dst_instance}/schema/import",
            headers=dst_headers,
            json={"schema": bundle, "mode": "merge", "dry_run": True},
        )
        assert resp.status_code == 200, resp.text
        report = resp.json()
        assert report["valid"] is True
        assert report["created"] == {}
        order = report["apply_order"]
        # Заказы (независимый) раньше Клиентов (formula-агрегация на Заказы)
        assert order["templates"].index("Заказы") < order["templates"].index("Клиенты")
        # T1 раньше T2 (target T1 = source T2)
        assert order["triggers"].index("T1 upsert клиента") < order["triggers"].index(
            "T2 награда за повторный заказ"
        )

        # dry_run ничего не создал
        dst_bundle = await _export(test_client, dst_instance, dst_headers)
        assert dst_bundle["templates"] == []

    @pytest.mark.asyncio
    async def test_invalid_bundle_rejected_without_changes(
        self, test_client, create_test_environment
    ):
        _, instance_uuid, headers = await create_test_environment()
        broken_bundle = {
            "format_version": 1,
            "templates": [
                {
                    "uuid": "11111111-1111-1111-1111-111111111111",
                    "name": "Одинокий",
                    "schema": {"title": {"type": "string", "required": True}},
                }
            ],
            "triggers": [
                {
                    "name": "битый триггер",
                    "trigger_type": "AUTOMATION",
                    "event_type": "MANUAL",
                    "source_template_uuid": "99999999-9999-9999-9999-999999999999",
                    "target_template_uuid": "11111111-1111-1111-1111-111111111111",
                    "payload_ast": {"type": "literal", "value": True},
                    "action_name": "test_action",
                    "action_params": {"required_text": "x"},
                }
            ],
            "widgets": [],
            "policies": [{"template_name": "Не существует"}],
            "notification_templates": [],
        }
        resp = await test_client.post(
            f"/instances/{instance_uuid}/schema/import",
            headers=headers,
            json={"schema": broken_bundle, "mode": "merge"},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        issue_types = {issue["object_type"] for issue in detail["errors"]}
        assert "trigger" in issue_types
        assert "policy" in issue_types

        # Атомарность: ничего не создано
        bundle_after = await _export(test_client, instance_uuid, headers)
        assert bundle_after["templates"] == []

    @pytest.mark.asyncio
    async def test_merge_name_collision_rejected(
        self, test_client, create_test_environment
    ):
        user_uuid, src_instance, headers = await create_test_environment()
        await _build_loyalty_instance(test_client, src_instance, headers)
        bundle = await _export(test_client, src_instance, headers)

        # Импорт в ТОТ ЖЕ инстанс в merge — все имена уже заняты
        resp = await test_client.post(
            f"/instances/{src_instance}/schema/import",
            headers=headers,
            json={"schema": bundle, "mode": "merge"},
        )
        assert resp.status_code == 422, resp.text
        details = resp.json()["detail"]["errors"]
        assert any("уже существует" in issue["detail"] for issue in details)


class TestImportEndToEnd:

    @pytest.mark.asyncio
    async def test_merge_into_fresh_instance_makes_working_crm(
        self, test_client, create_test_environment
    ):
        """Главный сценарий: после загрузки схемы CRM полностью работоспособна —
        каскад триггеров стреляет, формулы считаются, виджет отдаёт данные."""
        user_uuid, src_instance, src_headers = await create_test_environment()
        src_ids = await _build_loyalty_instance(test_client, src_instance, src_headers)
        bundle = await _export(test_client, src_instance, src_headers)

        dst_user, dst_instance, dst_headers = await create_test_environment()
        resp = await test_client.post(
            f"/instances/{dst_instance}/schema/import",
            headers=dst_headers,
            json={"schema": bundle, "mode": "merge"},
        )
        assert resp.status_code == 200, resp.text
        report = resp.json()
        assert report["valid"] is True, report
        assert report["created"] == {
            "templates": 3,
            "notification_templates": 1,
            "policies": 1,
            "widgets": 1,
            "triggers": 2,
        }
        # Ремап: все 3 старых uuid получили новые, и новые отличаются
        assert set(report["id_map"].keys()) == set(src_ids)
        assert all(old != new for old, new in report["id_map"].items())

        new_orders_id = report["id_map"][src_ids[0]]
        new_clients_id = report["id_map"][src_ids[1]]
        new_rewards_id = report["id_map"][src_ids[2]]
        base = f"/instances/{dst_instance}"

        # Заказ №1 → T1 создаёт клиента; orders_count (formula по НОВОМУ uuid) = 1
        phone = "+375290000001"
        order1 = await test_client.post(
            f"{base}/templates/{new_orders_id}/notes",
            headers=dst_headers,
            json={"data": {"client_phone": phone, "client_name": "Ира", "amount": 50}},
        )
        assert order1.status_code == 201, order1.text

        clients_list = await test_client.get(
            f"{base}/templates/{new_clients_id}/notes", headers=dst_headers
        )
        assert clients_list.json()["total"] == 1
        client_record = clients_list.json()["results"][0]
        assert client_record["data"]["phone"] == phone

        # Заказ №2 → T1 upsert-UPDATE клиента → каскад T2 → награда
        order2 = await test_client.post(
            f"{base}/templates/{new_orders_id}/notes",
            headers=dst_headers,
            json={"data": {"client_phone": phone, "client_name": "Ира", "amount": 70}},
        )
        assert order2.status_code == 201, order2.text

        rewards_list = await test_client.get(
            f"{base}/templates/{new_rewards_id}/notes", headers=dst_headers
        )
        assert rewards_list.json()["total"] >= 1
        assert rewards_list.json()["results"][0]["data"]["reason"] == "повторный заказ"

        # Виджет в новом инстансе работает по новому шаблону
        widgets_export = await _export(test_client, dst_instance, dst_headers)
        widget_cfg = widgets_export["widgets"][0]
        assert widget_cfg["target_template_uuid"] == new_orders_id

    @pytest.mark.asyncio
    async def test_replace_swaps_config_and_allows_rollback(
        self, test_client, create_test_environment
    ):
        """replace: текущий конфиг сносится, bundle встаёт; previous_schema из
        ответа загружается обратно тем же эндпоинтом («загрузить предыдущую»)."""
        user_uuid, src_instance, src_headers = await create_test_environment()
        await _build_loyalty_instance(test_client, src_instance, src_headers)
        loyalty_bundle = await _export(test_client, src_instance, src_headers)

        # Целевой инстанс с СОБСТВЕННЫМ мини-конфигом
        _, dst_instance, dst_headers = await create_test_environment()
        own_template = await test_client.post(
            f"/instances/{dst_instance}/templates",
            headers=dst_headers,
            json={
                "name": "Старый каталог",
                "schema": {"sku": {"type": "string", "required": True}},
            },
        )
        assert own_template.status_code == 201

        replace_resp = await test_client.post(
            f"/instances/{dst_instance}/schema/import",
            headers=dst_headers,
            json={"schema": loyalty_bundle, "mode": "replace"},
        )
        assert replace_resp.status_code == 200, replace_resp.text
        report = replace_resp.json()
        assert report["valid"] is True, report
        assert report["deleted"]["templates"] == 1
        previous = report["previous_schema"]
        assert [t["name"] for t in previous["templates"]] == ["Старый каталог"]

        after_replace = await _export(test_client, dst_instance, dst_headers)
        assert {t["name"] for t in after_replace["templates"]} == {
            "Заказы",
            "Клиенты",
            "Награды",
        }

        # Откат: загружаем previous_schema в режиме replace
        rollback_resp = await test_client.post(
            f"/instances/{dst_instance}/schema/import",
            headers=dst_headers,
            json={"schema": previous, "mode": "replace"},
        )
        assert rollback_resp.status_code == 200, rollback_resp.text
        after_rollback = await _export(test_client, dst_instance, dst_headers)
        assert [t["name"] for t in after_rollback["templates"]] == ["Старый каталог"]
        assert after_rollback["triggers"] == []
        assert after_rollback["widgets"] == []

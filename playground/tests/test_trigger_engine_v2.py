import pytest

from main import app
from mongo.db import get_mongo_db
from triggers.exceptions.action import AutomationExecutionError
from triggers.models import EventType
from triggers.service import AutomationService


async def _mongo_db_from_test_app():
    override = app.dependency_overrides[get_mongo_db]
    async for db in override():
        return db


def _client_upsert_trigger(orders_id: str, clients_id: str):
    return {
        "name": "v2 upsert client",
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_CREATE",
        "source_template_uuid": orders_id,
        "target_template_uuid": clients_id,
        "condition_ast": {
            "type": "binary_op",
            "operator": "gt",
            "left": {"type": "field", "value": "client_phone"},
            "right": {"type": "literal", "value": ""},
        },
        "payload_ast": {
            "type": "object",
            "fields": {
                "phone": {"type": "field", "value": "client_phone"},
                "name": {"type": "field", "value": "client_name"},
            },
        },
        "action_name": "UPSERT_RECORD",
        "action_params": {
            "search_fields": ["phone"],
        },
        "action_mapping_ast": {
            "type": "object",
            "fields": {
                "phone": {"type": "field", "value": "client_phone"},
                "name": {"type": "field", "value": "client_name"},
            },
        },
    }


class TestTriggerEngineV2BusinessCases:
    @pytest.mark.asyncio
    async def test_case_1_order_create_upserts_client(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        clients_id = env["clients_template_uuid"]
        products_id = env["products_template_uuid"]
        orders_id = env["orders_template_uuid"]

        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json=_client_upsert_trigger(orders_id, clients_id),
            headers=headers,
        )
        assert trigger_resp.status_code == 201, trigger_resp.text

        product_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{products_id}/notes",
            json={"data": {"name": "Кольцо", "quantity_left": 10, "cost": 100}},
            headers=headers,
        )
        assert product_resp.status_code == 201

        order_url = f"/instances/{instance_uuid}/templates/{orders_id}/notes"
        phone = "+375291234567"
        first_order = {
            "data": {
                "product_list": [{"target_uuid": product_resp.json()["_id"], "qty": 1}],
                "client_phone": phone,
                "client_name": "Анна",
                "pickup": True,
                "cost": 100,
                "source": "сайт",
                "payment": "картой",
                "real_cost": 100,
            }
        }
        create_resp = await test_client.post(
            order_url, json=first_order, headers=headers
        )
        assert create_resp.status_code == 201, create_resp.text

        clients_url = f"/instances/{instance_uuid}/templates/{clients_id}/notes"
        clients_resp = await test_client.get(clients_url, headers=headers)
        assert clients_resp.status_code == 200
        assert clients_resp.json()["total"] == 1
        assert clients_resp.json()["results"][0]["data"]["phone"] == phone
        assert clients_resp.json()["results"][0]["data"]["name"] == "Анна"

        second_order = first_order.copy()
        second_order["data"] = {**first_order["data"], "client_name": "Анна Новая"}
        update_resp = await test_client.post(
            order_url, json=second_order, headers=headers
        )
        assert update_resp.status_code == 201, update_resp.text

        clients_resp = await test_client.get(clients_url, headers=headers)
        assert clients_resp.json()["total"] == 1
        assert clients_resp.json()["results"][0]["data"]["name"] == "Анна Новая"

    @pytest.mark.asyncio
    async def test_case_2_live_eval_returns_product_suggestions(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        products_id = env["products_template_uuid"]

        products_url = f"/instances/{instance_uuid}/templates/{products_id}/notes"
        await test_client.post(
            products_url,
            json={"data": {"name": "Кольцо серебро", "quantity_left": 3, "cost": 100}},
            headers=headers,
        )
        await test_client.post(
            products_url,
            json={
                "data": {"name": "Кольцо распродано", "quantity_left": 0, "cost": 90}
            },
            headers=headers,
        )
        await test_client.post(
            products_url,
            json={"data": {"name": "Серьги", "quantity_left": 5, "cost": 120}},
            headers=headers,
        )

        trigger_payload = {
            "name": "v2 product suggestions",
            "trigger_type": "LIVE_EVAL",
            "event_type": "MANUAL",
            "source_template_uuid": products_id,
            "target_template_uuid": products_id,
            "condition_ast": {
                "type": "binary_op",
                "operator": "gt",
                "left": {"type": "input"},
                "right": {"type": "literal", "value": ""},
            },
            "payload_ast": {
                "type": "query",
                "target_template_uuid": products_id,
                "filters": [
                    {
                        "field": "name",
                        "operator": "contains",
                        "value": {"type": "input"},
                    },
                    {
                        "field": "quantity_left",
                        "operator": "gt",
                        "value": {"type": "literal", "value": 0},
                    },
                ],
                "return_fields": ["name", "quantity_left"],
            },
            "action_name": "RETURN_TO_CALLER",
        }
        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json=trigger_payload,
            headers=headers,
        )
        assert trigger_resp.status_code == 201, trigger_resp.text

        eval_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/{trigger_resp.json()['id']}/evaluate",
            json={"context_data": {}, "manual_input": "кольцо"},
            headers=headers,
        )
        assert eval_resp.status_code == 200, eval_resp.text
        names = [item["data"]["name"] for item in eval_resp.json()["result"]]
        assert names == ["Кольцо серебро"]

    @pytest.mark.asyncio
    async def test_case_3_paid_order_decrements_stock_with_one_bulk_write(
        self, test_client, setup_crm_environment, monkeypatch
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        products_id = env["products_template_uuid"]
        orders_id = env["orders_template_uuid"]

        trigger_payload = {
            "name": "v2 decrement product stock",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_UPDATE",
            "source_template_uuid": orders_id,
            "target_template_uuid": products_id,
            "condition_ast": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "payment"},
                "right": {"type": "literal", "value": "картой"},
            },
            "payload_ast": {"type": "field", "value": "product_list"},
            "action_name": "UPDATE_RECORD",
            "action_mapping_ast": {
                "type": "object",
                "fields": {
                    "_id": {"type": "field", "value": "current_item.target_uuid"},
                    "quantity_left": {
                        "type": "object",
                        "fields": {
                            "op": {"type": "literal", "value": "inc"},
                            "value": {
                                "type": "binary_op",
                                "operator": "multiply",
                                "left": {"type": "field", "value": "current_item.qty"},
                                "right": {"type": "literal", "value": -1},
                            },
                        },
                    },
                },
            },
        }
        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json=trigger_payload,
            headers=headers,
        )
        assert trigger_resp.status_code == 201, trigger_resp.text

        products_url = f"/instances/{instance_uuid}/templates/{products_id}/notes"
        p1 = await test_client.post(
            products_url,
            json={"data": {"name": "Кольцо", "quantity_left": 10, "cost": 100}},
            headers=headers,
        )
        p2 = await test_client.post(
            products_url,
            json={"data": {"name": "Серьги", "quantity_left": 5, "cost": 200}},
            headers=headers,
        )

        db = await _mongo_db_from_test_app()
        collection_cls = type(db["records"])
        original_bulk_write = collection_cls.bulk_write
        calls = []

        async def counted_bulk_write(self, requests, *args, **kwargs):
            calls.append(requests)
            return await original_bulk_write(self, requests, *args, **kwargs)

        monkeypatch.setattr(collection_cls, "bulk_write", counted_bulk_write)

        orders_url = f"/instances/{instance_uuid}/templates/{orders_id}/notes"
        order_resp = await test_client.post(
            orders_url,
            json={
                "data": {
                    "product_list": [
                        {"target_uuid": p1.json()["_id"], "qty": 2},
                        {"target_uuid": p2.json()["_id"], "qty": 3},
                    ],
                    "client_phone": "+375291111111",
                    "client_name": "Покупатель",
                    "pickup": True,
                    "cost": 700,
                    "source": "сайт",
                    "payment": "картой",
                    "real_cost": 700,
                }
            },
            headers=headers,
        )
        assert order_resp.status_code == 201

        patch_resp = await test_client.patch(
            f"{orders_url}/{order_resp.json()['_id']}",
            json={"data": {"real_cost": 700}},
            headers=headers,
        )
        assert patch_resp.status_code == 200, patch_resp.text
        assert len(calls) == 1
        assert len(calls[0]) == 2

        products_resp = await test_client.get(products_url, headers=headers)
        quantities = {
            item["data"]["name"]: item["data"]["quantity_left"]
            for item in products_resp.json()["results"]
        }
        assert quantities["Кольцо"] == 8
        assert quantities["Серьги"] == 2


class TestTriggerEngineV2Validation:
    @pytest.mark.asyncio
    async def test_bulk_notification_rejects_value_payload(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        orders_id = env["orders_template_uuid"]
        products_id = env["products_template_uuid"]

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                "name": "bad bulk payload",
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": orders_id,
                "target_template_uuid": products_id,
                "condition_ast": {"type": "literal", "value": True},
                "payload_ast": {"type": "field", "value": "client_phone"},
                "action_name": "SEND_BULK_NOTIFICATION",
            },
            headers=headers,
        )
        assert response.status_code == 422
        details = response.json()["details"]
        assert details["field"] == "payload_ast"
        assert details["expected"] == "LIST"
        assert details["got"] == "VALUE"

    @pytest.mark.asyncio
    async def test_cycle_detection_reports_cycle_path(
        self, test_client, create_test_environment
    ):
        _, instance_uuid, headers = await create_test_environment()
        templates_url = f"/instances/{instance_uuid}/templates"
        a = await test_client.post(
            templates_url,
            json={"name": "A", "schema": {"value": {"type": "string"}}},
            headers=headers,
        )
        b = await test_client.post(
            templates_url,
            json={"name": "B", "schema": {"value": {"type": "string"}}},
            headers=headers,
        )
        a_id = a.json()["_id"]
        b_id = b.json()["_id"]

        base_trigger = {
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "condition_ast": {"type": "literal", "value": True},
            "payload_ast": {
                "type": "object",
                "fields": {"value": {"type": "field", "value": "value"}},
            },
            "action_name": "UPSERT_RECORD",
            "action_mapping_ast": {
                "type": "object",
                "fields": {"value": {"type": "field", "value": "value"}},
            },
        }
        first = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                **base_trigger,
                "name": "A to B",
                "source_template_uuid": a_id,
                "target_template_uuid": b_id,
                "action_params": {
                    "search_fields": ["value"],
                },
            },
            headers=headers,
        )
        assert first.status_code == 201, first.text

        second = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                **base_trigger,
                "name": "B to A",
                "source_template_uuid": b_id,
                "target_template_uuid": a_id,
                "action_params": {
                    "search_fields": ["value"],
                },
            },
            headers=headers,
        )
        assert second.status_code == 422
        cycle_path = second.json()["details"]["got"]
        assert a_id in cycle_path
        assert b_id in cycle_path

    @pytest.mark.asyncio
    async def test_dml_action_params_target_mismatch_is_rejected(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        orders_id = env["orders_template_uuid"]
        clients_id = env["clients_template_uuid"]
        products_id = env["products_template_uuid"]

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                "name": "bad dml target mismatch",
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": orders_id,
                "target_template_uuid": clients_id,
                "condition_ast": {"type": "literal", "value": True},
                "payload_ast": {
                    "type": "object",
                    "fields": {
                        "phone": {"type": "field", "value": "client_phone"},
                    },
                },
                "action_name": "UPSERT_RECORD",
                "action_params": {
                    "target_template_uuid": products_id,
                    "search_fields": ["phone"],
                },
                "action_mapping_ast": {
                    "type": "object",
                    "fields": {
                        "phone": {"type": "field", "value": "client_phone"},
                    },
                },
            },
            headers=headers,
        )
        assert response.status_code == 422
        details = response.json()["details"]
        assert details["field"] == "action_params.target_template_uuid"
        assert details["expected"] == clients_id
        assert details["got"] == products_id

    @pytest.mark.asyncio
    async def test_condition_ast_must_be_boolean(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        orders_id = env["orders_template_uuid"]
        products_id = env["products_template_uuid"]

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                "name": "bad condition",
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": orders_id,
                "target_template_uuid": products_id,
                "condition_ast": {"type": "field", "value": "client_phone"},
                "payload_ast": {"type": "field", "value": "client_phone"},
                "action_name": "test_action",
                "action_params": {"required_text": "x"},
            },
            headers=headers,
        )
        assert response.status_code == 422
        assert response.json()["details"]["field"] == "condition_ast"

    @pytest.mark.asyncio
    async def test_payload_return_type_is_server_computed(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        orders_id = env["orders_template_uuid"]
        products_id = env["products_template_uuid"]

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                "name": "server computed return type",
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": orders_id,
                "target_template_uuid": products_id,
                "condition_ast": {"type": "literal", "value": True},
                "payload_ast": {"type": "field", "value": "client_phone"},
                "payload_return_type": "LIST",
                "action_name": "test_action",
                "action_params": {"required_text": "x"},
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        assert response.json()["payload_return_type"] == "VALUE"

    @pytest.mark.asyncio
    async def test_boolean_field_condition_infers_boolean(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        orders_id = env["orders_template_uuid"]
        products_id = env["products_template_uuid"]

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                "name": "boolean field condition",
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": orders_id,
                "target_template_uuid": products_id,
                "condition_ast": {"type": "field", "value": "pickup"},
                "payload_ast": {"type": "field", "value": "client_phone"},
                "action_name": "test_action",
                "action_params": {"required_text": "x"},
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text

    @pytest.mark.asyncio
    async def test_regex_match_condition_infers_boolean(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        orders_id = env["orders_template_uuid"]
        products_id = env["products_template_uuid"]

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                "name": "regex condition",
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": orders_id,
                "target_template_uuid": products_id,
                "condition_ast": {
                    "type": "string_op",
                    "operator": "regex_match",
                    "left": {"type": "field", "value": "client_phone"},
                    "right": {"type": "literal", "value": "^\\+"},
                },
                "payload_ast": {"type": "field", "value": "client_phone"},
                "action_name": "test_action",
                "action_params": {"required_text": "x"},
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text

    @pytest.mark.asyncio
    async def test_comparison_rejects_list_operand(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        orders_id = env["orders_template_uuid"]
        products_id = env["products_template_uuid"]

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                "name": "bad list comparison",
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": orders_id,
                "target_template_uuid": products_id,
                "condition_ast": {"type": "literal", "value": True},
                "payload_ast": {
                    "type": "binary_op",
                    "operator": "eq",
                    "left": {"type": "field", "value": "product_list"},
                    "right": {"type": "literal", "value": 0},
                },
                "action_name": "test_action",
                "action_params": {"required_text": "x"},
            },
            headers=headers,
        )
        assert response.status_code == 422
        details = response.json()["details"]
        assert details["field"] == "payload_ast.left"
        assert details["expected"] == "scalar operand"
        assert details["got"] == "LIST"

    @pytest.mark.asyncio
    async def test_malformed_ast_uses_trigger_validation_error_shape(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        orders_id = env["orders_template_uuid"]
        products_id = env["products_template_uuid"]

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                "name": "malformed ast",
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": orders_id,
                "target_template_uuid": products_id,
                "condition_ast": {"type": "literal", "value": True},
                "payload_ast": {"type": "unknown_node"},
                "action_name": "test_action",
                "action_params": {"required_text": "x"},
            },
            headers=headers,
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error_code"] == "TRIGGER_RECORD_VALIDATION_ERROR"
        assert body["details"]["field"] == "payload_ast"

    @pytest.mark.asyncio
    async def test_patch_edit_introduces_cycle(
        self, test_client, create_test_environment
    ):
        _, instance_uuid, headers = await create_test_environment()
        templates_url = f"/instances/{instance_uuid}/templates"
        a = await test_client.post(
            templates_url,
            json={"name": "Patch A", "schema": {"value": {"type": "string"}}},
            headers=headers,
        )
        b = await test_client.post(
            templates_url,
            json={"name": "Patch B", "schema": {"value": {"type": "string"}}},
            headers=headers,
        )
        c = await test_client.post(
            templates_url,
            json={"name": "Patch C", "schema": {"value": {"type": "string"}}},
            headers=headers,
        )
        a_id = a.json()["_id"]
        b_id = b.json()["_id"]
        c_id = c.json()["_id"]

        def dml_trigger(source_id: str, target_id: str, name: str):
            return {
                "name": name,
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": source_id,
                "target_template_uuid": target_id,
                "condition_ast": {"type": "literal", "value": True},
                "payload_ast": {
                    "type": "object",
                    "fields": {"value": {"type": "field", "value": "value"}},
                },
                "action_name": "UPSERT_RECORD",
                "action_params": {
                    "search_fields": ["value"],
                },
                "action_mapping_ast": {
                    "type": "object",
                    "fields": {"value": {"type": "field", "value": "value"}},
                },
            }

        first = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json=dml_trigger(a_id, b_id, "A to B"),
            headers=headers,
        )
        assert first.status_code == 201, first.text

        second = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json=dml_trigger(b_id, c_id, "B to C"),
            headers=headers,
        )
        assert second.status_code == 201, second.text

        patch = await test_client.patch(
            f"/instances/{instance_uuid}/triggers/{second.json()['id']}",
            json={
                "target_template_uuid": a_id,
                "action_params": {
                    "search_fields": ["value"],
                },
            },
            headers=headers,
        )
        assert patch.status_code == 422
        cycle_path = patch.json()["details"]["got"]
        assert a_id in cycle_path
        assert b_id in cycle_path

    @pytest.mark.asyncio
    async def test_evaluate_accepts_legacy_context_input_value(
        self, test_client, setup_crm_environment
    ):
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        products_id = env["products_template_uuid"]

        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json={
                "name": "legacy input evaluate",
                "trigger_type": "LIVE_EVAL",
                "event_type": "MANUAL",
                "source_template_uuid": products_id,
                "target_template_uuid": products_id,
                "condition_ast": {
                    "type": "binary_op",
                    "operator": "gt",
                    "left": {"type": "input"},
                    "right": {"type": "literal", "value": ""},
                },
                "payload_ast": {"type": "input"},
                "action_name": "RETURN_TO_CALLER",
            },
            headers=headers,
        )
        assert trigger_resp.status_code == 201, trigger_resp.text

        eval_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/{trigger_resp.json()['id']}/evaluate",
            json={"context_data": {"__input_value__": "legacy search"}},
            headers=headers,
        )
        assert eval_resp.status_code == 200
        assert eval_resp.json()["result"] == "legacy search"

    @pytest.mark.asyncio
    async def test_runtime_cascade_depth_guard_raises_typed_error(self):
        with pytest.raises(AutomationExecutionError):
            await AutomationService.handle_event(
                pg_session=None,
                mongo_db=None,
                instance_uuid="00000000-0000-0000-0000-000000000001",
                template_uuid="00000000-0000-0000-0000-000000000002",
                event_type=EventType.ON_RECORD_UPDATE,
                document={},
                cascade_depth=6,
            )

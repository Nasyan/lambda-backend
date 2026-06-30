import json
import os
from pathlib import Path
from typing import Any

import pytest

from triggers.service import AutomationService

pytestmark = pytest.mark.asyncio

SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "instance_snapshots"


def lit(value: Any) -> dict[str, Any]:
    return {"type": "literal", "value": value}


def fld(value: str) -> dict[str, Any]:
    return {"type": "field", "value": value}


def obj(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"type": "object", "fields": fields}


def binop(operator: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"type": "binary_op", "operator": operator, "left": left, "right": right}


def logic(
    operator: str, left: dict[str, Any], right: dict[str, Any] | None = None
) -> dict[str, Any]:
    node = {"type": "logical_op", "operator": operator, "left": left}
    if right is not None:
        node["right"] = right
    return node


def query(
    template_uuid: str,
    filters: list[dict[str, Any]],
    return_fields: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "query",
        "target_template_uuid": template_uuid,
        "filters": filters,
        "limit": limit,
    }
    if return_fields is not None:
        node["return_fields"] = return_fields
    return node


async def create_template(
    test_client, instance_uuid: str, headers: dict[str, str], name: str, schema: dict
) -> str:
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        headers=headers,
        json={"name": name, "schema": schema},
    )
    assert response.status_code == 201, response.text
    return response.json()["_id"]


async def create_trigger(
    test_client,
    instance_uuid: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    expected_status: int = 201,
) -> dict[str, Any]:
    response = await test_client.post(
        f"/instances/{instance_uuid}/triggers",
        headers=headers,
        json=payload,
    )
    assert response.status_code == expected_status, response.text
    return response.json() if response.content else {}


async def create_record(
    test_client,
    instance_uuid: str,
    template_uuid: str,
    headers: dict[str, str],
    data: dict[str, Any],
) -> dict[str, Any]:
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        headers=headers,
        json={"data": data},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def patch_record(
    test_client,
    instance_uuid: str,
    template_uuid: str,
    record_uuid: str,
    headers: dict[str, str],
    data: dict[str, Any],
) -> dict[str, Any]:
    response = await test_client.patch(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes/{record_uuid}",
        headers=headers,
        json={"data": data},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def list_records(
    test_client, instance_uuid: str, template_uuid: str, headers: dict[str, str]
) -> list[dict[str, Any]]:
    response = await test_client.get(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["results"]


async def records_total(
    test_client, instance_uuid: str, template_uuid: str, headers: dict[str, str]
) -> int:
    response = await test_client.get(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["total"]


async def find_record(
    test_client,
    instance_uuid: str,
    template_uuid: str,
    headers: dict[str, str],
    field: str,
    value: Any,
) -> dict[str, Any] | None:
    for record in await list_records(
        test_client, instance_uuid, template_uuid, headers
    ):
        if record["data"].get(field) == value:
            return record
    return None


async def export_bundle(
    test_client, instance_uuid: str, headers: dict[str, str]
) -> dict:
    response = await test_client.get(
        f"/instances/{instance_uuid}/schema/export", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _replace_template_refs(value: Any, id_to_name: dict[str, str]) -> Any:
    if isinstance(value, str):
        return id_to_name.get(value, value)
    if isinstance(value, dict):
        return {
            key: _replace_template_refs(item, id_to_name) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_template_refs(item, id_to_name) for item in value]
    return value


def normalize_bundle_for_snapshot(bundle: dict[str, Any]) -> dict[str, Any]:
    id_to_name = {
        template["uuid"]: template["name"] for template in bundle["templates"]
    }
    normalized = {
        "format_version": bundle["format_version"],
        "templates": [],
        "triggers": [],
        "widgets": [],
        "policies": sorted(bundle["policies"], key=lambda item: item["template_name"]),
        "notification_templates": sorted(
            bundle["notification_templates"], key=lambda item: item["name"]
        ),
    }
    for template in bundle["templates"]:
        normalized["templates"].append(
            {
                "uuid": template["name"],
                "name": template["name"],
                "schema": _replace_template_refs(template["schema"], id_to_name),
            }
        )
    for trigger in bundle["triggers"]:
        normalized["triggers"].append(_replace_template_refs(trigger, id_to_name))
    for widget in bundle["widgets"]:
        normalized["widgets"].append(_replace_template_refs(widget, id_to_name))

    normalized["templates"].sort(key=lambda item: item["name"])
    normalized["triggers"].sort(key=lambda item: item["name"])
    normalized["widgets"].sort(key=lambda item: item["name"])
    return normalized


async def assert_export_matches_snapshot(
    test_client,
    instance_uuid: str,
    headers: dict[str, str],
    snapshot_name: str,
) -> None:
    actual = normalize_bundle_for_snapshot(
        await export_bundle(test_client, instance_uuid, headers)
    )
    # Снепшот нормализован (uuid → имена шаблонов) и детерминирован между
    # прогонами. SNAPSHOT_REGEN=1 перезаписывает эталон из живого экспорта —
    # снепшоты служат входом для instance_schema roundtrip-тестов.
    snapshot_path = SNAPSHOT_DIR / snapshot_name
    if os.getenv("SNAPSHOT_REGEN") == "1":
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(actual, indent=2, ensure_ascii=False))
        return
    expected = json.loads(snapshot_path.read_text())
    assert actual == expected


def loyalty_accrual_trigger(orders_id: str, clients_id: str) -> dict[str, Any]:
    return {
        "name": "loyalty: paid order accrues points",
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_CREATE",
        "source_template_uuid": orders_id,
        "target_template_uuid": clients_id,
        "condition_ast": binop("eq", fld("payment"), lit("card")),
        "payload_ast": obj({"phone": fld("client_phone"), "amount": fld("amount")}),
        "action_name": "UPSERT_RECORD",
        "action_params": {"search_fields": ["phone"]},
        "action_mapping_ast": obj(
            {
                "phone": fld("client_phone"),
                "name": fld("client_name"),
                "points": obj({"op": lit("inc"), "value": fld("amount")}),
            }
        ),
    }


def loyalty_reward_trigger(clients_id: str, rewards_id: str) -> dict[str, Any]:
    crossed_gold_threshold = logic(
        "and",
        binop("gt", fld("$new.points"), lit(100)),
        logic("not", binop("gt", fld("$old.points"), lit(100))),
    )
    return {
        "name": "loyalty: issue gold reward once",
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_UPDATE",
        "source_template_uuid": clients_id,
        "target_template_uuid": rewards_id,
        "condition_ast": crossed_gold_threshold,
        "payload_ast": obj({"phone": fld("phone")}),
        "action_name": "INSERT_RECORD",
        "action_mapping_ast": obj({"phone": fld("phone"), "reward": lit("GOLD")}),
    }


async def test_loyalty_program_accrues_points_and_rewards_once(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    orders_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Loyalty Orders",
        {
            "client_phone": {"type": "string", "required": True},
            "client_name": {"type": "string", "required": True},
            "amount": {"type": "number", "required": True},
            "payment": {"type": "string", "required": True},
        },
    )
    clients_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Loyalty Clients",
        {
            "phone": {"type": "string", "required": True},
            "name": {"type": "string", "required": False},
            "points": {"type": "number", "required": False},
            "orders_count": {
                "type": "formula",
                "required": False,
                "ast": {
                    "type": "aggregation",
                    "target_template_uuid": orders_id,
                    "filter_field": "client_phone",
                    "filter_value": fld("phone"),
                    "agg_function": "count",
                    "agg_field": None,
                },
            },
        },
    )
    rewards_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Loyalty Rewards",
        {
            "phone": {"type": "string", "required": True},
            "reward": {"type": "string", "required": True},
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        loyalty_accrual_trigger(orders_id, clients_id),
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        loyalty_reward_trigger(clients_id, rewards_id),
    )

    phone = "+375290000101"
    await create_record(
        test_client,
        instance_uuid,
        orders_id,
        headers,
        {
            "client_phone": phone,
            "client_name": "Ira",
            "amount": 70,
            "payment": "cash",
        },
    )
    assert await records_total(test_client, instance_uuid, clients_id, headers) == 0

    await create_record(
        test_client,
        instance_uuid,
        orders_id,
        headers,
        {
            "client_phone": phone,
            "client_name": "Ira",
            "amount": 50,
            "payment": "card",
        },
    )
    client = await find_record(
        test_client, instance_uuid, clients_id, headers, "phone", phone
    )
    assert client is not None
    assert float(client["data"]["points"]) == 50.0
    assert await records_total(test_client, instance_uuid, rewards_id, headers) == 0

    await create_record(
        test_client,
        instance_uuid,
        orders_id,
        headers,
        {
            "client_phone": phone,
            "client_name": "Ira",
            "amount": 60,
            "payment": "card",
        },
    )
    client = await find_record(
        test_client, instance_uuid, clients_id, headers, "phone", phone
    )
    assert float(client["data"]["points"]) == 110.0
    assert client["version"] == 2
    rewards = await list_records(test_client, instance_uuid, rewards_id, headers)
    assert len(rewards) == 1
    assert rewards[0]["data"] == {"phone": phone, "reward": "GOLD"}

    await create_record(
        test_client,
        instance_uuid,
        orders_id,
        headers,
        {
            "client_phone": phone,
            "client_name": "Ira",
            "amount": 40,
            "payment": "card",
        },
    )
    client = await find_record(
        test_client, instance_uuid, clients_id, headers, "phone", phone
    )
    assert float(client["data"]["points"]) == 150.0
    assert await records_total(test_client, instance_uuid, rewards_id, headers) == 1
    await assert_export_matches_snapshot(
        test_client, instance_uuid, headers, "loyalty_snapshot.json"
    )


def stock_decrement_trigger(orders_id: str, products_id: str) -> dict[str, Any]:
    return {
        "name": "inventory: decrement stock by shipped lines",
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_CREATE",
        "source_template_uuid": orders_id,
        "target_template_uuid": products_id,
        "condition_ast": binop("eq", fld("payment"), lit("card")),
        "payload_ast": fld("lines"),
        "action_name": "UPDATE_RECORD",
        "action_mapping_ast": obj(
            {
                "_id": fld("current_item.target_uuid"),
                "stock": obj(
                    {
                        "op": lit("inc"),
                        "value": binop("multiply", fld("current_item.qty"), lit(-1)),
                    }
                ),
            }
        ),
    }


def reorder_trigger(products_id: str, reorders_id: str) -> dict[str, Any]:
    return {
        "name": "inventory: create reorder below threshold",
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_UPDATE",
        "source_template_uuid": products_id,
        "target_template_uuid": reorders_id,
        "condition_ast": binop("lt", fld("stock"), fld("threshold")),
        "payload_ast": obj({"sku": fld("sku")}),
        "action_name": "UPSERT_RECORD",
        "action_params": {"search_fields": ["sku"]},
        "action_mapping_ast": obj(
            {
                "sku": fld("sku"),
                "status": lit("REORDER_NEEDED"),
                "last_stock": fld("stock"),
            }
        ),
    }


async def test_inventory_shipment_decrements_stock_and_upserts_reorder(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    products_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Inventory Products",
        {
            "sku": {"type": "string", "required": True},
            "name": {"type": "string", "required": True},
            "stock": {"type": "number", "required": True},
            "threshold": {"type": "number", "required": True},
        },
    )
    orders_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Inventory Shipments",
        {
            "shipment_no": {"type": "string", "required": True},
            "payment": {"type": "string", "required": True},
            "lines": {
                "type": "relation_list",
                "target_template_uuid": products_id,
                "required": True,
            },
        },
    )
    reorders_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Inventory Reorders",
        {
            "sku": {"type": "string", "required": True},
            "status": {"type": "string", "required": True},
            "last_stock": {"type": "number", "required": False},
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        stock_decrement_trigger(orders_id, products_id),
    )
    await create_trigger(
        test_client, instance_uuid, headers, reorder_trigger(products_id, reorders_id)
    )
    product = await create_record(
        test_client,
        instance_uuid,
        products_id,
        headers,
        {"sku": "RING-7", "name": "Ring", "stock": 5, "threshold": 3},
    )

    await create_record(
        test_client,
        instance_uuid,
        orders_id,
        headers,
        {
            "shipment_no": "S-1",
            "payment": "card",
            "lines": [{"target_uuid": product["_id"], "sku": "RING-7", "qty": 2}],
        },
    )
    product_after = await find_record(
        test_client, instance_uuid, products_id, headers, "sku", "RING-7"
    )
    assert float(product_after["data"]["stock"]) == 3.0
    assert await records_total(test_client, instance_uuid, reorders_id, headers) == 0

    await create_record(
        test_client,
        instance_uuid,
        orders_id,
        headers,
        {
            "shipment_no": "S-2",
            "payment": "card",
            "lines": [{"target_uuid": product["_id"], "sku": "RING-7", "qty": 1}],
        },
    )
    reorder = await find_record(
        test_client, instance_uuid, reorders_id, headers, "sku", "RING-7"
    )
    assert reorder is not None
    assert reorder["data"]["status"] == "REORDER_NEEDED"
    assert float(reorder["data"]["last_stock"]) == 2.0

    await create_record(
        test_client,
        instance_uuid,
        orders_id,
        headers,
        {
            "shipment_no": "S-3",
            "payment": "card",
            "lines": [{"target_uuid": product["_id"], "sku": "RING-7", "qty": 1}],
        },
    )
    assert await records_total(test_client, instance_uuid, reorders_id, headers) == 1
    reorder = await find_record(
        test_client, instance_uuid, reorders_id, headers, "sku", "RING-7"
    )
    assert reorder["version"] == 2
    assert float(reorder["data"]["last_stock"]) == 1.0
    await assert_export_matches_snapshot(
        test_client, instance_uuid, headers, "inventory_snapshot.json"
    )


async def test_helpdesk_sla_assigns_agents_and_cron_escalates_p1(
    test_client, create_test_environment, db_session, mongo_db
):
    _, instance_uuid, headers = await create_test_environment()
    tickets_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Helpdesk Tickets",
        {
            "title": {"type": "string", "required": True},
            "priority": {"type": "string", "required": True},
            "status": {"type": "string", "required": True},
        },
    )
    assignments_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Helpdesk Assignments",
        {
            "ticket_title": {"type": "string", "required": True},
            "assignee": {"type": "string", "required": True},
            "due_at": {"type": "datetime", "required": True},
        },
    )
    escalations_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Helpdesk Escalations",
        {
            "ticket_title": {"type": "string", "required": True},
            "level": {"type": "string", "required": True},
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "helpdesk: assign every new ticket",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "source_template_uuid": tickets_id,
            "target_template_uuid": assignments_id,
            "payload_ast": obj({"ticket_title": fld("title")}),
            "action_name": "INSERT_RECORD",
            "action_mapping_ast": obj(
                {
                    "ticket_title": fld("title"),
                    "assignee": lit("tier-1"),
                    "due_at": {
                        "type": "date_op",
                        "operator": "add_days",
                        "left": {"type": "date_op", "operator": "now"},
                        "right": lit(1),
                    },
                }
            ),
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "helpdesk: cron escalate open p1",
            "trigger_type": "AUTOMATION",
            "event_type": "CRON",
            "cron_expression": "*/5 * * * *",
            "source_template_uuid": tickets_id,
            "target_template_uuid": escalations_id,
            "condition_ast": logic(
                "and",
                binop("eq", fld("priority"), lit("P1")),
                binop("eq", fld("status"), lit("open")),
            ),
            "payload_ast": obj({"ticket_title": fld("title")}),
            "action_name": "UPSERT_RECORD",
            "action_params": {"search_fields": ["ticket_title"]},
            "action_mapping_ast": obj(
                {"ticket_title": fld("title"), "level": lit("SLA_BREACH")}
            ),
        },
    )
    await create_record(
        test_client,
        instance_uuid,
        tickets_id,
        headers,
        {"title": "Password reset", "priority": "P3", "status": "open"},
    )
    await create_record(
        test_client,
        instance_uuid,
        tickets_id,
        headers,
        {"title": "Checkout down", "priority": "P1", "status": "open"},
    )
    assignments = await list_records(
        test_client, instance_uuid, assignments_id, headers
    )
    assert sorted(item["data"]["ticket_title"] for item in assignments) == [
        "Checkout down",
        "Password reset",
    ]
    assert all("T" in item["data"]["due_at"] for item in assignments)

    await AutomationService.process_cron_triggers(db_session, mongo_db)
    escalations = await list_records(
        test_client, instance_uuid, escalations_id, headers
    )
    assert len(escalations) == 1
    assert escalations[0]["data"] == {
        "ticket_title": "Checkout down",
        "level": "SLA_BREACH",
    }


async def test_crm_lead_routing_sends_regions_to_different_queues(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    leads_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "CRM Leads",
        {
            "name": {"type": "string", "required": True},
            "region": {"type": "string", "required": True},
            "budget": {"type": "number", "required": True},
        },
    )
    north_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "CRM North Queue",
        {
            "lead_name": {"type": "string", "required": True},
            "budget": {"type": "number", "required": True},
        },
    )
    south_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "CRM South Queue",
        {
            "lead_name": {"type": "string", "required": True},
            "budget": {"type": "number", "required": True},
        },
    )
    for region, target_id in [("north", north_id), ("south", south_id)]:
        await create_trigger(
            test_client,
            instance_uuid,
            headers,
            {
                "name": f"crm: route {region} leads",
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": leads_id,
                "target_template_uuid": target_id,
                "condition_ast": binop("eq", fld("region"), lit(region)),
                "payload_ast": obj({"lead_name": fld("name"), "budget": fld("budget")}),
                "action_name": "INSERT_RECORD",
                "action_mapping_ast": obj(
                    {"lead_name": fld("name"), "budget": fld("budget")}
                ),
            },
        )

    await create_record(
        test_client,
        instance_uuid,
        leads_id,
        headers,
        {"name": "Nina", "region": "north", "budget": 100000},
    )
    await create_record(
        test_client,
        instance_uuid,
        leads_id,
        headers,
        {"name": "Will", "region": "west", "budget": 200000},
    )
    await create_record(
        test_client,
        instance_uuid,
        leads_id,
        headers,
        {"name": "Sara", "region": "south", "budget": 150000},
    )
    north_records = await list_records(test_client, instance_uuid, north_id, headers)
    south_records = await list_records(test_client, instance_uuid, south_id, headers)
    assert [item["data"]["lead_name"] for item in north_records] == ["Nina"]
    assert [item["data"]["lead_name"] for item in south_records] == ["Sara"]


async def test_ecommerce_order_reserves_each_cart_line_and_calculates_total(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    products_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Shop Products",
        {
            "sku": {"type": "string", "required": True},
            "price": {"type": "number", "required": True},
        },
    )
    orders_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Shop Orders",
        {
            "order_no": {"type": "string", "required": True},
            "items": {
                "type": "relation_list",
                "target_template_uuid": products_id,
                "required": True,
            },
            "total": {
                "type": "formula",
                "required": False,
                "ast": {
                    "type": "array_reduce",
                    "array_field": "items",
                    "agg_function": "sum",
                    "item_expression": binop("multiply", fld("qty"), fld("price")),
                },
            },
        },
    )
    reservations_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Shop Reservations",
        {
            "order_no": {"type": "string", "required": True},
            "sku": {"type": "string", "required": True},
            "qty": {"type": "number", "required": True},
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "shop: reserve every cart line",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "source_template_uuid": orders_id,
            "target_template_uuid": reservations_id,
            "payload_ast": fld("items"),
            "action_name": "INSERT_RECORD",
            "action_mapping_ast": obj(
                {
                    "order_no": fld("order_no"),
                    "sku": fld("current_item.sku"),
                    "qty": fld("current_item.qty"),
                }
            ),
        },
    )
    p1 = await create_record(
        test_client, instance_uuid, products_id, headers, {"sku": "A", "price": 10}
    )
    p2 = await create_record(
        test_client, instance_uuid, products_id, headers, {"sku": "B", "price": 5}
    )
    order = await create_record(
        test_client,
        instance_uuid,
        orders_id,
        headers,
        {
            "order_no": "SO-1",
            "items": [
                {"target_uuid": p1["_id"], "sku": "A", "qty": 2, "price": 10},
                {"target_uuid": p2["_id"], "sku": "B", "qty": 3, "price": 5},
            ],
        },
    )
    assert float(order["data"]["total"]) == 35.0
    reservations = await list_records(
        test_client, instance_uuid, reservations_id, headers
    )
    assert sorted(
        (item["data"]["sku"], item["data"]["qty"]) for item in reservations
    ) == [
        ("A", 2),
        ("B", 3),
    ]


async def test_subscription_billing_renews_and_refund_suspends_subscription(
    test_client, create_test_environment, db_session, mongo_db
):
    _, instance_uuid, headers = await create_test_environment()
    subscriptions_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Billing Subscriptions",
        {
            "account_id": {"type": "string", "required": True},
            "status": {"type": "string", "required": True},
            "renewals_count": {"type": "number", "required": False},
            "paid_until": {"type": "datetime", "required": False},
        },
    )
    payments_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Billing Payments",
        {
            "account_id": {"type": "string", "required": True},
            "subscription_id": {"type": "string", "required": True},
            "amount": {"type": "number", "required": True},
            "status": {"type": "string", "required": True},
        },
    )
    reminders_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Billing Renewal Reminders",
        {
            "account_id": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
        },
    )
    subscription = await create_record(
        test_client,
        instance_uuid,
        subscriptions_id,
        headers,
        {"account_id": "ACME", "status": "active", "renewals_count": 0},
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "billing: paid payment renews subscription",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "source_template_uuid": payments_id,
            "target_template_uuid": subscriptions_id,
            "condition_ast": binop("eq", fld("status"), lit("paid")),
            "payload_ast": obj({"account_id": fld("account_id")}),
            "action_name": "UPSERT_RECORD",
            "action_params": {"search_fields": ["account_id"]},
            "action_mapping_ast": obj(
                {
                    "account_id": fld("account_id"),
                    "status": lit("active"),
                    "renewals_count": obj({"op": lit("inc"), "value": lit(1)}),
                    "paid_until": {
                        "type": "date_op",
                        "operator": "add_days",
                        "left": {"type": "date_op", "operator": "now"},
                        "right": lit(30),
                    },
                }
            ),
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "billing: refund suspends subscription",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_UPDATE",
            "source_template_uuid": payments_id,
            "target_template_uuid": subscriptions_id,
            "condition_ast": logic(
                "and",
                binop("eq", fld("$new.status"), lit("refunded")),
                binop("ne", fld("$old.status"), lit("refunded")),
            ),
            "payload_ast": obj({"subscription_id": fld("subscription_id")}),
            "action_name": "UPDATE_RECORD",
            "action_mapping_ast": obj(
                {"_id": fld("subscription_id"), "status": lit("suspended")}
            ),
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "billing: on-time active subscription reminder",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_TIME",
            "cron_expression": "0 9 * * *",
            "source_template_uuid": subscriptions_id,
            "target_template_uuid": reminders_id,
            "condition_ast": binop("eq", fld("status"), lit("active")),
            "payload_ast": obj({"account_id": fld("account_id")}),
            "action_name": "INSERT_RECORD",
            "action_mapping_ast": obj(
                {"account_id": fld("account_id"), "message": lit("renewal-check")}
            ),
        },
    )
    payment = await create_record(
        test_client,
        instance_uuid,
        payments_id,
        headers,
        {
            "account_id": "ACME",
            "subscription_id": subscription["_id"],
            "amount": 99,
            "status": "paid",
        },
    )
    renewed = await find_record(
        test_client, instance_uuid, subscriptions_id, headers, "account_id", "ACME"
    )
    assert renewed["_id"] == subscription["_id"]
    assert renewed["data"]["status"] == "active"
    assert float(renewed["data"]["renewals_count"]) == 1.0
    assert "T" in renewed["data"]["paid_until"]

    await AutomationService.process_cron_triggers(db_session, mongo_db)
    reminders = await list_records(test_client, instance_uuid, reminders_id, headers)
    assert len(reminders) == 1
    assert reminders[0]["data"]["account_id"] == "ACME"

    await patch_record(
        test_client,
        instance_uuid,
        payments_id,
        payment["_id"],
        headers,
        {"status": "refunded"},
    )
    suspended = await find_record(
        test_client, instance_uuid, subscriptions_id, headers, "account_id", "ACME"
    )
    assert suspended["data"]["status"] == "suspended"
    assert suspended["version"] >= 3


async def test_appointment_booking_upserts_slot_and_emits_notifications(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    requests_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Clinic Booking Requests",
        {
            "slot_code": {"type": "string", "required": True},
            "patient_phone": {"type": "string", "required": True},
            "patient_name": {"type": "string", "required": True},
        },
    )
    slots_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Clinic Slots",
        {
            "slot_code": {"type": "string", "required": True},
            "status": {"type": "string", "required": True},
            "patient_phone": {"type": "string", "required": False},
            "patient_name": {"type": "string", "required": False},
        },
    )
    notifications_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Clinic Notifications",
        {
            "slot_code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "clinic: book or update slot",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "source_template_uuid": requests_id,
            "target_template_uuid": slots_id,
            "payload_ast": obj({"slot_code": fld("slot_code")}),
            "action_name": "UPSERT_RECORD",
            "action_params": {"search_fields": ["slot_code"]},
            "action_mapping_ast": obj(
                {
                    "slot_code": fld("slot_code"),
                    "status": lit("booked"),
                    "patient_phone": fld("patient_phone"),
                    "patient_name": fld("patient_name"),
                }
            ),
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "clinic: notify booked slot",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_UPDATE",
            "source_template_uuid": slots_id,
            "target_template_uuid": notifications_id,
            "condition_ast": binop("eq", fld("status"), lit("booked")),
            "payload_ast": obj({"slot_code": fld("slot_code")}),
            "action_name": "INSERT_RECORD",
            "action_mapping_ast": obj(
                {"slot_code": fld("slot_code"), "message": lit("slot-booked")}
            ),
        },
    )
    await create_record(
        test_client,
        instance_uuid,
        requests_id,
        headers,
        {
            "slot_code": "2026-07-01T10:00",
            "patient_phone": "+375291111111",
            "patient_name": "Nina",
        },
    )
    slot = await find_record(
        test_client, instance_uuid, slots_id, headers, "slot_code", "2026-07-01T10:00"
    )
    assert slot is not None
    assert slot["data"]["patient_name"] == "Nina"
    assert (
        await records_total(test_client, instance_uuid, notifications_id, headers) == 1
    )

    await create_record(
        test_client,
        instance_uuid,
        requests_id,
        headers,
        {
            "slot_code": "2026-07-01T10:00",
            "patient_phone": "+375292222222",
            "patient_name": "Mila",
        },
    )
    slots = await list_records(test_client, instance_uuid, slots_id, headers)
    assert len(slots) == 1
    assert slots[0]["data"]["patient_name"] == "Mila"
    assert slots[0]["version"] == 2
    assert (
        await records_total(test_client, instance_uuid, notifications_id, headers) == 2
    )


async def test_education_grades_recalculate_average_and_award_excellence(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    grades_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "School Grades",
        {
            "student_code": {"type": "string", "required": True},
            "score": {"type": "number", "required": True},
        },
    )
    students_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "School Students",
        {
            "student_code": {"type": "string", "required": True},
            "name": {"type": "string", "required": True},
            "avg_score": {
                "type": "formula",
                "required": False,
                "ast": {
                    "type": "aggregation",
                    "target_template_uuid": grades_id,
                    "filter_field": "student_code",
                    "filter_value": fld("student_code"),
                    "agg_function": "avg",
                    "agg_field": "score",
                },
            },
        },
    )
    honors_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "School Honors",
        {
            "student_code": {"type": "string", "required": True},
            "badge": {"type": "string", "required": True},
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "school: award excellent average",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_UPDATE",
            "source_template_uuid": students_id,
            "target_template_uuid": honors_id,
            "condition_ast": binop("gt", fld("avg_score"), lit(90)),
            "payload_ast": obj({"student_code": fld("student_code")}),
            "action_name": "UPSERT_RECORD",
            "action_params": {"search_fields": ["student_code"]},
            "action_mapping_ast": obj(
                {"student_code": fld("student_code"), "badge": lit("EXCELLENT")}
            ),
        },
    )
    await create_record(
        test_client,
        instance_uuid,
        grades_id,
        headers,
        {"student_code": "S-1", "score": 95},
    )
    await create_record(
        test_client,
        instance_uuid,
        grades_id,
        headers,
        {"student_code": "S-1", "score": 85},
    )
    student = await create_record(
        test_client,
        instance_uuid,
        students_id,
        headers,
        {"student_code": "S-1", "name": "Ada"},
    )
    assert float(student["data"]["avg_score"]) == 90.0
    assert await records_total(test_client, instance_uuid, honors_id, headers) == 0

    await create_record(
        test_client,
        instance_uuid,
        grades_id,
        headers,
        {"student_code": "S-1", "score": 100},
    )
    student = await patch_record(
        test_client,
        instance_uuid,
        students_id,
        student["_id"],
        headers,
        {"name": "Ada Lovelace"},
    )
    assert round(float(student["data"]["avg_score"]), 2) == 93.33
    honor = await find_record(
        test_client, instance_uuid, honors_id, headers, "student_code", "S-1"
    )
    assert honor is not None
    assert honor["data"]["badge"] == "EXCELLENT"


async def test_real_estate_matching_supports_manual_return_and_live_eval(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    requests_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Realty Requests",
        {
            "district": {"type": "string", "required": True},
            "budget": {"type": "number", "required": True},
        },
    )
    properties_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Realty Properties",
        {
            "title": {"type": "string", "required": True},
            "district": {"type": "string", "required": True},
            "price": {"type": "number", "required": True},
        },
    )
    await create_record(
        test_client,
        instance_uuid,
        properties_id,
        headers,
        {"title": "Green flat", "district": "center", "price": 240000},
    )
    await create_record(
        test_client,
        instance_uuid,
        properties_id,
        headers,
        {"title": "Luxury flat", "district": "center", "price": 500000},
    )
    await create_record(
        test_client,
        instance_uuid,
        properties_id,
        headers,
        {"title": "Lake house", "district": "north", "price": 220000},
    )
    manual_trigger = await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "realty: manual center shortlist",
            "trigger_type": "AUTOMATION",
            "event_type": "MANUAL",
            "source_template_uuid": requests_id,
            "target_template_uuid": properties_id,
            "payload_ast": query(
                properties_id,
                [
                    {"field": "district", "operator": "eq", "value": lit("center")},
                    {"field": "price", "operator": "lt", "value": lit(300000)},
                ],
                return_fields=["title", "price"],
            ),
            "action_name": "RETURN_TO_CALLER",
        },
    )
    exec_response = await test_client.post(
        f"/instances/{instance_uuid}/triggers/{manual_trigger['id']}/execute",
        headers=headers,
    )
    assert exec_response.status_code == 200, exec_response.text
    result = exec_response.json()["execution_details"]["result"]
    assert [item["data"]["title"] for item in result] == ["Green flat"]

    live_trigger = await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "realty: live buyer-specific shortlist",
            "trigger_type": "LIVE_EVAL",
            "event_type": "MANUAL",
            "source_template_uuid": requests_id,
            "target_template_uuid": properties_id,
            "payload_ast": query(
                properties_id,
                [
                    {"field": "district", "operator": "eq", "value": fld("district")},
                    {"field": "price", "operator": "lt", "value": fld("budget")},
                ],
                return_fields=["title", "price"],
            ),
            "action_name": "RETURN_TO_CALLER",
        },
    )
    eval_response = await test_client.post(
        f"/instances/{instance_uuid}/triggers/{live_trigger['id']}/evaluate",
        headers=headers,
        json={"context_data": {"district": "north", "budget": 250000}},
    )
    assert eval_response.status_code == 200, eval_response.text
    assert [item["data"]["title"] for item in eval_response.json()["result"]] == [
        "Lake house"
    ]


async def test_event_registration_cascades_to_participant_badge_and_email(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    registrations_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Event Registrations",
        {
            "email": {"type": "string", "required": True},
            "full_name": {"type": "string", "required": True},
            "ticket_type": {"type": "string", "required": True},
        },
    )
    participants_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Event Participants",
        {
            "email": {"type": "string", "required": True},
            "full_name": {"type": "string", "required": True},
            "ticket_type": {"type": "string", "required": True},
        },
    )
    badges_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Event Badges",
        {
            "email": {"type": "string", "required": True},
            "badge_code": {"type": "string", "required": True},
        },
    )
    emails_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Event Email Queue",
        {
            "email": {"type": "string", "required": True},
            "template": {"type": "string", "required": True},
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "event: registration upserts participant",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "source_template_uuid": registrations_id,
            "target_template_uuid": participants_id,
            "payload_ast": obj({"email": fld("email")}),
            "action_name": "UPSERT_RECORD",
            "action_params": {"search_fields": ["email"]},
            "action_mapping_ast": obj(
                {
                    "email": fld("email"),
                    "full_name": fld("full_name"),
                    "ticket_type": fld("ticket_type"),
                }
            ),
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "event: participant update creates badge",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_UPDATE",
            "source_template_uuid": participants_id,
            "target_template_uuid": badges_id,
            "payload_ast": obj({"email": fld("email")}),
            "action_name": "INSERT_RECORD",
            "action_mapping_ast": obj(
                {"email": fld("email"), "badge_code": fld("ticket_type")}
            ),
        },
    )
    await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "event: badge queues email",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "source_template_uuid": badges_id,
            "target_template_uuid": emails_id,
            "payload_ast": obj({"email": fld("email")}),
            "action_name": "INSERT_RECORD",
            "action_mapping_ast": obj(
                {"email": fld("email"), "template": lit("badge-ready")}
            ),
        },
    )
    await create_record(
        test_client,
        instance_uuid,
        registrations_id,
        headers,
        {
            "email": "guest@example.test",
            "full_name": "Guest One",
            "ticket_type": "VIP",
        },
    )
    participant = await find_record(
        test_client,
        instance_uuid,
        participants_id,
        headers,
        "email",
        "guest@example.test",
    )
    assert participant is not None
    assert participant["data"]["ticket_type"] == "VIP"
    badges = await list_records(test_client, instance_uuid, badges_id, headers)
    emails = await list_records(test_client, instance_uuid, emails_id, headers)
    assert len(badges) == 1
    assert badges[0]["data"] == {"email": "guest@example.test", "badge_code": "VIP"}
    assert len(emails) == 1
    assert emails[0]["data"] == {
        "email": "guest@example.test",
        "template": "badge-ready",
    }


async def test_stored_column_trigger_metadata_and_boolean_test_action(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    invoices_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Finance Invoices",
        {
            "amount": {"type": "number", "required": True},
            "tax": {
                "type": "formula",
                "required": False,
                "ast": binop("multiply", fld("amount"), lit(0.2)),
            },
            "approved": {"type": "string", "required": False},
        },
    )
    stored_trigger = await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "finance: stored tax formula metadata",
            "trigger_type": "STORED_COLUMN",
            "source_template_uuid": invoices_id,
            "target_template_uuid": invoices_id,
            "target_field": "tax",
            "payload_ast": binop("multiply", fld("amount"), lit(0.2)),
        },
    )
    manual_boolean = await create_trigger(
        test_client,
        instance_uuid,
        headers,
        {
            "name": "finance: boolean approval smoke",
            "trigger_type": "AUTOMATION",
            "event_type": "MANUAL",
            "source_template_uuid": invoices_id,
            "target_template_uuid": invoices_id,
            "payload_ast": binop("gt", lit(10), lit(5)),
            "action_name": "test_action",
            "action_params": {"required_text": "boolean-ok", "send_attempts": 2},
        },
    )
    invoice = await create_record(
        test_client, instance_uuid, invoices_id, headers, {"amount": 250}
    )
    assert float(invoice["data"]["tax"]) == 50.0

    template_response = await test_client.get(
        f"/instances/{instance_uuid}/templates/{invoices_id}", headers=headers
    )
    assert template_response.status_code == 200, template_response.text
    tax_triggers = template_response.json()["schema"]["tax"]["triggers"]
    assert tax_triggers[0]["trigger_id"] == stored_trigger["id"]
    assert tax_triggers[0]["trigger_type"] == "STORED_COLUMN"
    assert tax_triggers[0]["event"] == "onCalculate"

    exec_response = await test_client.post(
        f"/instances/{instance_uuid}/triggers/{manual_boolean['id']}/execute",
        headers=headers,
    )
    assert exec_response.status_code == 200, exec_response.text
    action_result = exec_response.json()["execution_details"]["result"]
    assert action_result["executed_records"] == 1
    assert "boolean-ok" in action_result["logs"][0]

import pytest

from core.tests.test_client_scenarios import (
    binop,
    create_record,
    create_template,
    create_trigger,
    export_bundle,
    fld,
    find_record,
    lit,
    logic,
    loyalty_accrual_trigger,
    loyalty_reward_trigger,
    normalize_bundle_for_snapshot,
    obj,
    patch_record,
    records_total,
    reorder_trigger,
    stock_decrement_trigger,
)

pytestmark = pytest.mark.asyncio


async def _create_widget(test_client, instance_uuid, headers, name, template_uuid):
    response = await test_client.post(
        f"/instances/{instance_uuid}/widgets",
        headers=headers,
        json={
            "name": name,
            "target_template_uuid": template_uuid,
            "widget_type": "BAR",
            "chart_config": {
                "axis_x": {"field": "client_phone", "type": "categorical"},
                "axis_y": {"field": "_id", "aggregation": "COUNT"},
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_policy(test_client, instance_uuid, headers, template_name):
    response = await test_client.post(
        f"/instances/{instance_uuid}/storefront-configs",
        headers=headers,
        json={
            "template_name": template_name,
            "read_filters": {},
            "read_mask": ["client_phone", "amount"],
            "write_mask": ["client_phone", "client_name", "amount", "payment", "lines"],
        },
    )
    assert response.status_code in (200, 201), response.text


async def _create_notification(
    test_client, instance_uuid, headers, source_template_uuid
):
    response = await test_client.post(
        f"/instances/{instance_uuid}/notifications/templates",
        headers=headers,
        json={
            "name": "Large order digest",
            "title": "Order {{data.client_phone}}",
            "body": "Amount {{data.amount}}",
            "channels": ["crm"],
            "recipients_config": {"roles": ["CREATOR"]},
            "source_template_uuid": source_template_uuid,
        },
    )
    assert response.status_code in (200, 201), response.text


async def build_large_instance(test_client, instance_uuid, headers):
    products_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Large Products",
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
        "Large Orders",
        {
            "client_phone": {"type": "string", "required": True},
            "client_name": {"type": "string", "required": True},
            "amount": {"type": "number", "required": True},
            "payment": {"type": "string", "required": True},
            "lines": {
                "type": "relation_list",
                "target_template_uuid": products_id,
                "required": False,
            },
        },
    )

    clients_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Large Clients",
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
        "Large Rewards",
        {
            "phone": {"type": "string", "required": True},
            "reward": {"type": "string", "required": True},
        },
    )
    reorders_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Large Reorders",
        {
            "sku": {"type": "string", "required": True},
            "status": {"type": "string", "required": True},
            "last_stock": {"type": "number", "required": False},
        },
    )
    tickets_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Large Tickets",
        {
            "title": {"type": "string", "required": True},
            "priority": {"type": "string", "required": True},
            "status": {"type": "string", "required": True},
        },
    )
    escalations_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Large Escalations",
        {
            "ticket_title": {"type": "string", "required": True},
            "level": {"type": "string", "required": True},
        },
    )
    registrations_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Large Registrations",
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
        "Large Participants",
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
        "Large Badges",
        {
            "email": {"type": "string", "required": True},
            "badge_code": {"type": "string", "required": True},
        },
    )
    emails_id = await create_template(
        test_client,
        instance_uuid,
        headers,
        "Large Email Queue",
        {
            "email": {"type": "string", "required": True},
            "template": {"type": "string", "required": True},
        },
    )

    trigger_payloads = [
        loyalty_accrual_trigger(orders_id, clients_id),
        loyalty_reward_trigger(clients_id, rewards_id),
        stock_decrement_trigger(orders_id, products_id),
        reorder_trigger(products_id, reorders_id),
        {
            "name": "large: p1 ticket escalates",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "source_template_uuid": tickets_id,
            "target_template_uuid": escalations_id,
            "condition_ast": logic(
                "and",
                binop("eq", fld("priority"), lit("P1")),
                binop("eq", fld("status"), lit("open")),
            ),
            "payload_ast": obj({"ticket_title": fld("title")}),
            "action_name": "INSERT_RECORD",
            "action_mapping_ast": obj(
                {"ticket_title": fld("title"), "level": lit("IMMEDIATE")}
            ),
        },
        {
            "name": "large: registration upserts participant",
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
        {
            "name": "large: participant update creates badge",
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
        {
            "name": "large: badge queues email",
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
    ]
    for payload in trigger_payloads:
        await create_trigger(test_client, instance_uuid, headers, payload)

    await _create_widget(
        test_client, instance_uuid, headers, "Large orders by phone", orders_id
    )
    await _create_policy(test_client, instance_uuid, headers, "Large Orders")
    await _create_notification(test_client, instance_uuid, headers, orders_id)

    return {
        "orders": orders_id,
        "products": products_id,
        "clients": clients_id,
        "rewards": rewards_id,
        "reorders": reorders_id,
        "tickets": tickets_id,
        "escalations": escalations_id,
        "registrations": registrations_id,
        "participants": participants_id,
        "badges": badges_id,
        "emails": emails_id,
    }


async def _import_schema(test_client, instance_uuid, headers, bundle, mode="merge"):
    response = await test_client.post(
        f"/instances/{instance_uuid}/schema/import",
        headers=headers,
        json={"schema": bundle, "mode": mode},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["valid"] is True, report
    return report


async def _assert_imported_crm_works(
    test_client, instance_uuid, headers, id_map, source_ids
):
    orders_id = id_map[source_ids["orders"]]
    products_id = id_map[source_ids["products"]]
    clients_id = id_map[source_ids["clients"]]
    rewards_id = id_map[source_ids["rewards"]]
    reorders_id = id_map[source_ids["reorders"]]
    registrations_id = id_map[source_ids["registrations"]]
    participants_id = id_map[source_ids["participants"]]
    badges_id = id_map[source_ids["badges"]]
    emails_id = id_map[source_ids["emails"]]

    product = await create_record(
        test_client,
        instance_uuid,
        products_id,
        headers,
        {"sku": "BIG-1", "name": "Big Product", "stock": 2, "threshold": 2},
    )
    phone = "+375291234000"
    for number, amount in [("L-1", 70), ("L-2", 50)]:
        await create_record(
            test_client,
            instance_uuid,
            orders_id,
            headers,
            {
                "client_phone": phone,
                "client_name": "Imported Client",
                "amount": amount,
                "payment": "card",
                "lines": [{"target_uuid": product["_id"], "sku": "BIG-1", "qty": 1}],
            },
        )
    client = await find_record(
        test_client, instance_uuid, clients_id, headers, "phone", phone
    )
    assert client is not None
    assert float(client["data"]["points"]) == 120.0
    recalculated = await patch_record(
        test_client,
        instance_uuid,
        clients_id,
        client["_id"],
        headers,
        {"name": "Imported Client Recalc"},
    )
    assert float(recalculated["data"]["orders_count"]) == 2.0
    assert await records_total(test_client, instance_uuid, rewards_id, headers) == 1
    assert await records_total(test_client, instance_uuid, reorders_id, headers) == 1

    await create_record(
        test_client,
        instance_uuid,
        registrations_id,
        headers,
        {
            "email": "roundtrip@example.test",
            "full_name": "Round Trip",
            "ticket_type": "VIP",
        },
    )
    assert (
        await find_record(
            test_client,
            instance_uuid,
            participants_id,
            headers,
            "email",
            "roundtrip@example.test",
        )
        is not None
    )
    assert await records_total(test_client, instance_uuid, badges_id, headers) == 1
    assert await records_total(test_client, instance_uuid, emails_id, headers) == 1


async def test_large_instance_merge_import_remaps_and_runtime_still_works(
    test_client, create_test_environment
):
    _, source_instance, source_headers = await create_test_environment()
    source_ids = await build_large_instance(
        test_client, source_instance, source_headers
    )
    source_bundle = await export_bundle(test_client, source_instance, source_headers)
    assert len(source_bundle["templates"]) >= 8
    assert len(source_bundle["triggers"]) >= 8
    assert len(source_bundle["widgets"]) == 1
    assert len(source_bundle["policies"]) == 1

    _, target_instance, target_headers = await create_test_environment()
    report = await _import_schema(
        test_client, target_instance, target_headers, source_bundle, mode="merge"
    )
    assert report["created"]["templates"] == len(source_bundle["templates"])
    assert report["created"]["triggers"] == len(source_bundle["triggers"])
    assert set(report["id_map"]) == set(source_ids.values())
    assert all(old != new for old, new in report["id_map"].items())

    await _assert_imported_crm_works(
        test_client, target_instance, target_headers, report["id_map"], source_ids
    )


async def test_large_instance_roundtrip_export_is_stable_after_uuid_normalization(
    test_client, create_test_environment
):
    _, source_instance, source_headers = await create_test_environment()
    await build_large_instance(test_client, source_instance, source_headers)
    source_bundle = await export_bundle(test_client, source_instance, source_headers)

    _, target_instance, target_headers = await create_test_environment()
    await _import_schema(test_client, target_instance, target_headers, source_bundle)
    target_bundle = await export_bundle(test_client, target_instance, target_headers)

    source_normalized = normalize_bundle_for_snapshot(source_bundle)
    target_normalized = normalize_bundle_for_snapshot(target_bundle)
    assert target_normalized == source_normalized
    assert [t["name"] for t in target_normalized["templates"]] == [
        t["name"] for t in source_normalized["templates"]
    ]
    assert [t["name"] for t in target_normalized["triggers"]] == [
        t["name"] for t in source_normalized["triggers"]
    ]


async def test_large_instance_replace_can_rollback_to_previous_schema(
    test_client, create_test_environment
):
    _, source_instance, source_headers = await create_test_environment()
    await build_large_instance(test_client, source_instance, source_headers)
    source_bundle = await export_bundle(test_client, source_instance, source_headers)

    _, target_instance, target_headers = await create_test_environment()
    await create_template(
        test_client,
        target_instance,
        target_headers,
        "Rollback Seed",
        {"code": {"type": "string", "required": True}},
    )
    replace_report = await _import_schema(
        test_client, target_instance, target_headers, source_bundle, mode="replace"
    )
    assert replace_report["deleted"]["templates"] == 1
    previous_schema = replace_report["previous_schema"]
    assert [template["name"] for template in previous_schema["templates"]] == [
        "Rollback Seed"
    ]

    after_replace = await export_bundle(test_client, target_instance, target_headers)
    assert "Large Orders" in {
        template["name"] for template in after_replace["templates"]
    }
    assert len(after_replace["triggers"]) >= 8

    rollback_report = await _import_schema(
        test_client, target_instance, target_headers, previous_schema, mode="replace"
    )
    assert rollback_report["deleted"]["templates"] == len(source_bundle["templates"])
    after_rollback = await export_bundle(test_client, target_instance, target_headers)
    assert [template["name"] for template in after_rollback["templates"]] == [
        "Rollback Seed"
    ]
    assert after_rollback["triggers"] == []
    assert after_rollback["widgets"] == []
    assert after_rollback["policies"] == []

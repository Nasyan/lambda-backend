from uuid import UUID

import pytest
from sqlalchemy import func, select

from analytics.models import AnalyticsWidget
from notifications.models import NotificationTemplate
from policy.models import StorefrontPolicies
from triggers.admin_service import TriggerAdminService
from triggers.exceptions.validation import RecordValidationError
from triggers.models import Trigger

pytestmark = pytest.mark.asyncio


ORDERS_UUID = "11111111-1111-1111-1111-111111111111"
CLIENTS_UUID = "22222222-2222-2222-2222-222222222222"
A_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
C_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
MISSING_UUID = "99999999-9999-9999-9999-999999999999"


async def _import_bundle(test_client, instance_uuid, headers, bundle, mode="merge"):
    return await test_client.post(
        f"/instances/{instance_uuid}/schema/import",
        headers=headers,
        json={"schema": bundle, "mode": mode},
    )


async def _export_bundle(test_client, instance_uuid, headers):
    response = await test_client.get(
        f"/instances/{instance_uuid}/schema/export", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bundle(*, templates, triggers=None, widgets=None, policies=None, notifications=None):
    return {
        "format_version": 1,
        "templates": list(templates),
        "triggers": list(triggers or []),
        "widgets": list(widgets or []),
        "policies": list(policies or []),
        "notification_templates": list(notifications or []),
    }


def _template(uuid, name, schema):
    return {"uuid": uuid, "name": name, "schema": schema}


def _test_trigger(name, source_uuid, target_uuid):
    return {
        "name": name,
        "trigger_type": "AUTOMATION",
        "event_type": "MANUAL",
        "source_template_uuid": source_uuid,
        "target_template_uuid": target_uuid,
        "payload_ast": {"type": "literal", "value": True},
        "action_name": "test_action",
        "action_params": {"required_text": "ok"},
    }


def _aggregation_formula(target_uuid, agg_field):
    return {
        "type": "formula",
        "required": False,
        "ast": {
            "type": "aggregation",
            "target_template_uuid": target_uuid,
            "filter_field": "_id",
            "filter_value": {"type": "literal", "value": "all"},
            "agg_function": "sum",
            "agg_field": agg_field,
        },
    }


async def test_mutual_relations_import_success(test_client, create_test_environment):
    _, instance_uuid, headers = await create_test_environment()
    bundle = _bundle(
        templates=[
            _template(
                ORDERS_UUID,
                "Orders",
                {
                    "client_links": {
                        "type": "relation_list",
                        "target_template_uuid": CLIENTS_UUID,
                        "required": False,
                    }
                },
            ),
            _template(
                CLIENTS_UUID,
                "Clients",
                {
                    "order_links": {
                        "type": "relation_list",
                        "target_template_uuid": ORDERS_UUID,
                        "required": False,
                    }
                },
            ),
        ]
    )

    response = await _import_bundle(test_client, instance_uuid, headers, bundle)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["valid"] is True
    assert report["created"]["templates"] == 2

    exported = await _export_bundle(test_client, instance_uuid, headers)
    by_name = {template["name"]: template for template in exported["templates"]}
    assert by_name["Orders"]["schema"]["client_links"]["target_template_uuid"] == report[
        "id_map"
    ][CLIENTS_UUID]
    assert by_name["Clients"]["schema"]["order_links"]["target_template_uuid"] == report[
        "id_map"
    ][ORDERS_UUID]


async def test_deep_formula_chain_resolves(test_client, create_test_environment):
    _, instance_uuid, headers = await create_test_environment()
    bundle = _bundle(
        templates=[
            _template(A_UUID, "A", {"a_value": _aggregation_formula(B_UUID, "b_value")}),
            _template(B_UUID, "B", {"b_value": _aggregation_formula(C_UUID, "value")}),
            _template(C_UUID, "C", {"value": {"type": "number", "required": False}}),
        ]
    )

    response = await _import_bundle(test_client, instance_uuid, headers, bundle)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["valid"] is True
    order = report["apply_order"]["templates"]
    assert order.index("C") < order.index("B") < order.index("A")


async def test_formula_cycle_fails_with_cycles_and_no_writes(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    bundle = _bundle(
        templates=[
            _template(A_UUID, "A", {"a_value": _aggregation_formula(B_UUID, "b_value")}),
            _template(B_UUID, "B", {"b_value": _aggregation_formula(A_UUID, "a_value")}),
        ]
    )

    response = await _import_bundle(test_client, instance_uuid, headers, bundle)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["valid"] is False
    assert detail["created"] == {}
    assert detail["unresolved"]
    cycle_text = " ".join(" ".join(cycle) for cycle in detail["cycles"])
    assert f"template:formulas:{A_UUID}" in cycle_text
    assert f"template:formulas:{B_UUID}" in cycle_text

    exported = await _export_bundle(test_client, instance_uuid, headers)
    assert exported["templates"] == []


async def test_dangling_trigger_ref_rejected_without_writes(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    bundle = _bundle(
        templates=[
            _template(
                ORDERS_UUID,
                "Orders",
                {"amount": {"type": "number", "required": False}},
            )
        ],
        triggers=[_test_trigger("dangling", MISSING_UUID, ORDERS_UUID)],
    )

    response = await _import_bundle(test_client, instance_uuid, headers, bundle)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["created"] == {}

    exported = await _export_bundle(test_client, instance_uuid, headers)
    assert exported["templates"] == []
    assert exported["triggers"] == []


async def test_late_trigger_failure_rolls_back_postgres_and_mongo(
    test_client, create_test_environment, mongo_db, db_session, monkeypatch
):
    _, instance_uuid, headers = await create_test_environment()
    original_create_trigger = TriggerAdminService.create_trigger

    async def fail_last_trigger(self, instance_uuid, payload, user_uuid, **kwargs):
        if payload.name == "atomic: fail last":
            raise RecordValidationError(
                field="name",
                expected="non failing trigger",
                got=payload.name,
                detail="forced test failure",
            )
        return await original_create_trigger(
            self, instance_uuid, payload, user_uuid, **kwargs
        )

    monkeypatch.setattr(TriggerAdminService, "create_trigger", fail_last_trigger)

    bundle = _bundle(
        templates=[
            _template(
                ORDERS_UUID,
                "Orders",
                {
                    "client_phone": {"type": "string", "required": True},
                    "amount": {"type": "number", "required": False},
                },
            ),
            _template(
                CLIENTS_UUID,
                "Clients",
                {"phone": {"type": "string", "required": True}},
            ),
        ],
        widgets=[
            {
                "name": "Orders by phone",
                "target_template_uuid": ORDERS_UUID,
                "widget_type": "BAR",
                "chart_config": {
                    "axis_x": {"field": "client_phone", "type": "categorical"},
                    "axis_y": {"field": "_id", "aggregation": "COUNT"},
                },
            }
        ],
        policies=[
            {
                "template_name": "Orders",
                "read_filters": {},
                "read_mask": ["client_phone", "amount"],
                "write_mask": ["client_phone", "amount"],
            }
        ],
        notifications=[
            {
                "name": "Order notice",
                "title": "Order {{data.client_phone}}",
                "body": "Amount {{data.amount}}",
                "channels": ["crm"],
                "recipients_config": {"roles": ["CREATOR"]},
                "source_template_uuid": ORDERS_UUID,
            }
        ],
        triggers=[
            _test_trigger("atomic: ok", ORDERS_UUID, CLIENTS_UUID),
            _test_trigger("atomic: fail last", ORDERS_UUID, CLIENTS_UUID),
        ],
    )

    response = await _import_bundle(test_client, instance_uuid, headers, bundle)
    assert response.status_code != 200, response.text
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["created"] == {}

    exported = await _export_bundle(test_client, instance_uuid, headers)
    assert exported["templates"] == []
    assert exported["widgets"] == []
    assert exported["policies"] == []
    assert exported["notification_templates"] == []
    assert exported["triggers"] == []
    assert await mongo_db["templates"].count_documents(
        {"instance_uuid": str(instance_uuid)}
    ) == 0

    pg_instance_uuid = UUID(str(instance_uuid))
    for model in (
        AnalyticsWidget,
        StorefrontPolicies,
        NotificationTemplate,
        Trigger,
    ):
        result = await db_session.execute(
            select(func.count()).select_from(model).where(
                model.instance_uuid == pg_instance_uuid
            )
        )
        assert result.scalar_one() == 0

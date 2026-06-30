"""Бизнес E2E сценарии workflow-уведомлений.

Тесты специально идут через публичные API создания таблиц, записей,
шаблонов уведомлений и триггеров. Прямой вызов используется только для cron
обработчика: это тот же сервис, который вызывает Dramatiq worker под Redis-lock.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from jsonwebtoken.utils import encode_jwt
from main import app
from mongo.db import get_mongo_db
from triggers.service import AutomationService
from users.models import Users, UserRole


async def _mongo_db_from_test_app():
    override = app.dependency_overrides[get_mongo_db]
    async for db in override():
        return db


async def _create_employee(db_session, instance_uuid, name: str = "Менеджер"):
    employee_uuid = uuid4()
    employee = Users(
        uuid=employee_uuid,
        name=name,
        email=f"employee_{employee_uuid.hex[:8]}@test.com",
        hash_password="mock_password_hash_for_tests",
        role=UserRole.USER,
        active=True,
        instance_id=instance_uuid,
    )
    db_session.add(employee)
    await db_session.commit()
    token = encode_jwt(payload={"sub": str(employee_uuid)})
    return str(employee_uuid), {"Authorization": f"Bearer {token}"}


async def _create_crm_template(test_client, instance_uuid, headers, name, schema):
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": name, "schema": schema},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["_id"]


async def _create_notification_template(
    test_client,
    instance_uuid,
    headers,
    *,
    source_template_uuid,
    name,
    title,
    body,
    channels,
    recipients_config,
):
    response = await test_client.post(
        f"/instances/{instance_uuid}/notifications/templates",
        json={
            "name": name,
            "title": title,
            "body": body,
            "channels": channels,
            "recipients_config": recipients_config,
            "source_template_uuid": source_template_uuid,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["uuid"]


async def _create_notification_trigger(
    test_client,
    instance_uuid,
    headers,
    *,
    source_template_uuid,
    notification_template_uuid,
    name,
    event_type,
    condition_ast,
    payload_fields,
):
    response = await test_client.post(
        f"/instances/{instance_uuid}/triggers",
        json={
            "name": name,
            "trigger_type": "AUTOMATION",
            "event_type": event_type,
            "cron_expression": (
                "0 6 * * *" if event_type in {"CRON", "ON_TIME"} else None
            ),
            "source_template_uuid": source_template_uuid,
            "target_template_uuid": source_template_uuid,
            "condition_ast": condition_ast,
            "payload_ast": {
                "type": "object",
                "fields": {
                    field_name: {"type": "field", "value": field_name}
                    for field_name in payload_fields
                },
            },
            "action_name": "SEND_NOTIFICATION",
            "action_params": {
                "notification_template_uuid": notification_template_uuid,
            },
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _get_inbox(test_client, instance_uuid, headers):
    response = await test_client.get(
        f"/instances/{instance_uuid}/notifications/inbox",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestNotificationWorkflowEngine:
    @pytest.mark.asyncio
    async def test_reactive_status_change_notifies_selected_employee(
        self, test_client, create_test_environment, db_session
    ):
        # Что происходит: создаём CRM-таблицу клиентов, выбранного сотрудника,
        # notification template и ON_RECORD_UPDATE trigger. Затем меняем статус
        # клиента с "new" на "won" и проверяем CRM-колокольчик сотрудника.
        user_uuid, instance_uuid, creator_headers = await create_test_environment()
        employee_uuid, employee_headers = await _create_employee(
            db_session, instance_uuid, name="Ответственный менеджер"
        )

        clients_id = await _create_crm_template(
            test_client,
            instance_uuid,
            creator_headers,
            "Workflow Клиенты",
            {
                "name": {"type": "string", "required": True},
                "status": {"type": "string", "required": True},
            },
        )
        notification_uuid = await _create_notification_template(
            test_client,
            instance_uuid,
            creator_headers,
            source_template_uuid=clients_id,
            name="Смена статуса клиента",
            title="Клиент {{name}} сменил статус",
            body="Новый статус: {{status}}",
            channels=["crm"],
            recipients_config={"type": "users", "user_uuids": [employee_uuid]},
        )
        await _create_notification_trigger(
            test_client,
            instance_uuid,
            creator_headers,
            source_template_uuid=clients_id,
            notification_template_uuid=notification_uuid,
            name="Notify employee on status won",
            event_type="ON_RECORD_UPDATE",
            condition_ast={
                "type": "logical_op",
                "operator": "and",
                "left": {
                    "type": "binary_op",
                    "operator": "eq",
                    "left": {"type": "field", "value": "$new.status"},
                    "right": {"type": "literal", "value": "won"},
                },
                "right": {
                    "type": "binary_op",
                    "operator": "ne",
                    "left": {"type": "field", "value": "$old.status"},
                    "right": {"type": "field", "value": "$new.status"},
                },
            },
            payload_fields=["name", "status"],
        )

        notes_url = f"/instances/{instance_uuid}/templates/{clients_id}/notes"
        create_resp = await test_client.post(
            notes_url,
            json={"data": {"name": "Анна", "status": "new"}},
            headers=creator_headers,
        )
        assert create_resp.status_code == 201, create_resp.text

        update_resp = await test_client.patch(
            f"{notes_url}/{create_resp.json()['_id']}",
            json={"data": {"status": "won"}},
            headers=creator_headers,
        )
        assert update_resp.status_code == 200, update_resp.text

        inbox = await _get_inbox(test_client, instance_uuid, employee_headers)
        assert len(inbox) == 1
        assert inbox[0]["title"] == "Клиент Анна сменил статус"
        assert inbox[0]["body"] == "Новый статус: won"

    @pytest.mark.asyncio
    async def test_cron_birthday_emails_client_and_notifies_attached_employee(
        self, test_client, create_test_environment, db_session, monkeypatch
    ):
        # Что происходит: утренний cron находит клиентов с birth_date == today.
        # Один trigger ставит email-задачу клиенту через Dramatiq, второй trigger
        # создаёт CRM-колокольчик ответственному сотруднику из поля записи.
        user_uuid, instance_uuid, creator_headers = await create_test_environment()
        manager_uuid, manager_headers = await _create_employee(
            db_session, instance_uuid, name="Birthday manager"
        )
        sent_emails = []

        def capture_email(**kwargs):
            sent_emails.append(kwargs)

        monkeypatch.setattr("notifications.dispatcher.send_email.send", capture_email)

        clients_id = await _create_crm_template(
            test_client,
            instance_uuid,
            creator_headers,
            "Birthday Clients",
            {
                "name": {"type": "string", "required": True},
                "birth_date": {"type": "string", "required": True},
                "email": {"type": "string", "required": True},
                "responsible_user_uuid": {"type": "string", "required": True},
            },
        )
        email_template_uuid = await _create_notification_template(
            test_client,
            instance_uuid,
            creator_headers,
            source_template_uuid=clients_id,
            name="Birthday email",
            title="С днём рождения, {{name}}",
            body="Сегодня ваш день рождения: {{birth_date}}",
            channels=["email"],
            recipients_config={
                "type": "ast_tree",
                "tree": {
                    "type": "query",
                    "target_template_uuid": clients_id,
                    "filters": [
                        {
                            "field": "email",
                            "operator": "eq",
                            "value": {"type": "field", "value": "email"},
                        }
                    ],
                    "limit": 1,
                    "return_fields": ["email"],
                },
                "contact_field": "email",
            },
        )
        crm_template_uuid = await _create_notification_template(
            test_client,
            instance_uuid,
            creator_headers,
            source_template_uuid=clients_id,
            name="Birthday CRM",
            title="День рождения клиента {{name}}",
            body="Поздравить клиента сегодня",
            channels=["crm"],
            recipients_config={
                "type": "field_path",
                "field_path": "{{responsible_user_uuid}}",
            },
        )
        condition_today = {
            "type": "binary_op",
            "operator": "eq",
            "left": {"type": "field", "value": "birth_date"},
            "right": {"type": "literal", "value": date.today().isoformat()},
        }
        await _create_notification_trigger(
            test_client,
            instance_uuid,
            creator_headers,
            source_template_uuid=clients_id,
            notification_template_uuid=email_template_uuid,
            name="Cron birthday email",
            event_type="CRON",
            condition_ast=condition_today,
            payload_fields=["name", "birth_date", "email", "responsible_user_uuid"],
        )
        await _create_notification_trigger(
            test_client,
            instance_uuid,
            creator_headers,
            source_template_uuid=clients_id,
            notification_template_uuid=crm_template_uuid,
            name="Cron birthday crm",
            event_type="CRON",
            condition_ast=condition_today,
            payload_fields=["name", "birth_date", "email", "responsible_user_uuid"],
        )

        create_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{clients_id}/notes",
            json={
                "data": {
                    "name": "Ирина",
                    "birth_date": date.today().isoformat(),
                    "email": "irina@example.com",
                    "responsible_user_uuid": manager_uuid,
                }
            },
            headers=creator_headers,
        )
        assert create_resp.status_code == 201, create_resp.text

        mongo_db = await _mongo_db_from_test_app()
        await AutomationService.process_cron_triggers(db_session, mongo_db)

        assert len(sent_emails) == 1
        assert sent_emails[0]["receiver_email"] == "irina@example.com"
        assert sent_emails[0]["subject"] == "С днём рождения, Ирина"

        inbox = await _get_inbox(test_client, instance_uuid, manager_headers)
        assert len(inbox) == 1
        assert inbox[0]["title"] == "День рождения клиента Ирина"

    @pytest.mark.asyncio
    async def test_cron_forgotten_client_bulk_notifies_employee_group(
        self, test_client, create_test_environment, db_session
    ):
        # Что происходит: cron ищет клиентов без касания больше 3 дней и делает
        # bulk-уведомление всем активным сотрудникам инстанса через selection all.
        user_uuid, instance_uuid, creator_headers = await create_test_environment()
        first_uuid, first_headers = await _create_employee(
            db_session, instance_uuid, name="First manager"
        )
        second_uuid, second_headers = await _create_employee(
            db_session, instance_uuid, name="Second manager"
        )

        clients_id = await _create_crm_template(
            test_client,
            instance_uuid,
            creator_headers,
            "Forgotten Clients",
            {
                "name": {"type": "string", "required": True},
                "last_touch_at": {"type": "string", "required": True},
            },
        )
        notification_uuid = await _create_notification_template(
            test_client,
            instance_uuid,
            creator_headers,
            source_template_uuid=clients_id,
            name="Forgotten client bulk",
            title="Забытый клиент {{name}}",
            body="Последнее касание было {{last_touch_at}}",
            channels=["crm"],
            recipients_config={"type": "all_employees"},
        )
        await _create_notification_trigger(
            test_client,
            instance_uuid,
            creator_headers,
            source_template_uuid=clients_id,
            notification_template_uuid=notification_uuid,
            name="Cron forgotten client",
            event_type="CRON",
            condition_ast={
                "type": "binary_op",
                "operator": "gt",
                "left": {
                    "type": "date_op",
                    "operator": "diff_days",
                    "left": {"type": "date_op", "operator": "now"},
                    "right": {"type": "field", "value": "last_touch_at"},
                },
                "right": {"type": "literal", "value": 3},
            },
            payload_fields=["name", "last_touch_at"],
        )

        old_touch = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        recent_touch = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        notes_url = f"/instances/{instance_uuid}/templates/{clients_id}/notes"
        old_resp = await test_client.post(
            notes_url,
            json={"data": {"name": "Старый клиент", "last_touch_at": old_touch}},
            headers=creator_headers,
        )
        recent_resp = await test_client.post(
            notes_url,
            json={"data": {"name": "Свежий клиент", "last_touch_at": recent_touch}},
            headers=creator_headers,
        )
        assert old_resp.status_code == 201, old_resp.text
        assert recent_resp.status_code == 201, recent_resp.text

        mongo_db = await _mongo_db_from_test_app()
        await AutomationService.process_cron_triggers(db_session, mongo_db)

        first_inbox = await _get_inbox(test_client, instance_uuid, first_headers)
        second_inbox = await _get_inbox(test_client, instance_uuid, second_headers)
        assert [item["title"] for item in first_inbox] == [
            "Забытый клиент Старый клиент"
        ]
        assert [item["title"] for item in second_inbox] == [
            "Забытый клиент Старый клиент"
        ]

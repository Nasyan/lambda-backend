# tests/test_notifications.py
import pytest
import uuid
from notifications.models import NotificationTemplate  # Твоя SQLAlchemy модель


class TestNotification:

    @pytest.mark.asyncio
    async def test_notification_template_crud_lifecycle(
        self, test_client, create_test_environment
    ):
        """
        Полный цикл управления шаблонами уведомлений с новым префиксом инстанса:
        Создание -> Списки -> Деталка -> Обновление -> Удаление.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # Базовый URL для всех запросов этого инстанса
        base_url = f"/instances/{instance_uuid}/notifications"

        # 1. СОЗДАНИЕ ШАБЛОНА
        template_payload = {
            "name": "Уведомление о новой сделке",
            "title": "Новый лид: лид",
            "body": "Менеджер, проверьте карточку сделки с бюджетом 100 руб.",
            "channels": ["crm"],
            "recipients_config": {"type": "static", "uuids": [user_uuid]},
        }

        create_resp = await test_client.post(
            f"{base_url}/templates",
            json=template_payload,
            headers=headers,
        )

        assert create_resp.status_code == 201
        template_uuid = create_resp.json()["uuid"]
        assert template_uuid is not None

        # 2. ПОЛУЧЕНИЕ СПИСКА ВСЕХ ШАБЛОНОВ ИНСТАНСА
        list_resp = await test_client.get(f"{base_url}/templates", headers=headers)
        assert list_resp.status_code == 200
        templates_list = list_resp.json()
        assert len(templates_list) >= 1
        assert any(t["uuid"] == template_uuid for t in templates_list)

        # 3. ПОЛУЧЕНИЕ КОНКРЕТНОГО ШАБЛОНА ПО UUID
        get_resp = await test_client.get(
            f"{base_url}/templates/{template_uuid}", headers=headers
        )
        assert get_resp.status_code == 200
        template_data = get_resp.json()
        assert template_data["name"] == "Уведомление о новой сделке"
        assert "crm" in template_data["channels"]

        # 4. ЧАСТИЧНОЕ ОБНОВЛЕНИЕ ШАБЛОНА (PATCH)
        update_payload = {
            "name": "Уведомление о новой сделке (Изменено)",
            "channels": ["crm"],
        }
        patch_resp = await test_client.patch(
            f"{base_url}/templates/{template_uuid}",
            json=update_payload,
            headers=headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["status"] == "updated"

        # Проверяем, что изменения применились
        get_updated_resp = await test_client.get(
            f"{base_url}/templates/{template_uuid}", headers=headers
        )
        assert (
            get_updated_resp.json()["name"] == "Уведомление о новой сделке (Изменено)"
        )

        # 5. УДАЛЕНИЕ ШАБЛОНА
        delete_resp = await test_client.delete(
            f"{base_url}/templates/{template_uuid}", headers=headers
        )
        assert delete_resp.status_code == 204

        # Проверяем, что шаблона больше нет (404)
        get_deleted_resp = await test_client.get(
            f"{base_url}/templates/{template_uuid}", headers=headers
        )
        assert get_deleted_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_notification_inbox_empty_by_default(
        self, test_client, create_test_environment
    ):
        """Проверяем пустой инбокс по новому пути"""
        user_uuid, instance_uuid, headers = await create_test_environment()
        base_url = f"/instances/{instance_uuid}/notifications"

        inbox_resp = await test_client.get(f"{base_url}/inbox", headers=headers)

        assert inbox_resp.status_code == 200
        assert len(inbox_resp.json()) == 0

    @pytest.mark.asyncio
    async def test_mark_foreign_notification_as_read_not_found(
        self, test_client, create_test_environment
    ):
        """Проверка безопасности чужого уведомления"""
        user_uuid, instance_uuid, headers = await create_test_environment()
        base_url = f"/instances/{instance_uuid}/notifications"

        fake_notification_uuid = str(uuid.uuid4())

        response = await test_client.patch(
            f"{base_url}/inbox/{fake_notification_uuid}/read", headers=headers
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_notification_trigger_with_integrity_validation(
        self, test_client, create_test_environment
    ):
        """
        Тест 3: Сценарий сквозного связывания через триггер с проверкой Integrity.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # ==========================================================
        # 1. СОЗДАЕМ ШАБЛОН КЛИЕНТОВ
        # ==========================================================
        client_template_payload = {
            "name": "Клиенты Маркетинг",
            "schema": {
                "full_name": {"type": "string", "required": True},
                "phone": {"type": "string", "required": True},
                "orders_count": {"type": "number", "required": False, "default": 0},
            },
        }
        client_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=client_template_payload,
            headers=headers,
        )
        assert client_tpl_resp.status_code == 201
        client_template_uuid = client_tpl_resp.json()["_id"]

        # ==========================================================
        # 2. СОЗДАЕМ ШАБЛОН УВЕДОМЛЕНИЯ
        # ==========================================================
        notification_payload = {
            "name": "VIP Скидка для Клиента",
            "title": "Ура! Клиент получил статус VIP",
            "body": "Пользователь {{ client.full_name }} совершил уже {{ client.orders_count }} заказов. Выдайте бонус!",
            "channels": ["crm"],
            "recipients_config": {"type": "static", "uuids": [user_uuid]},
            "entity_mappings": {"client": client_template_uuid},
        }
        noti_resp = await test_client.post(
            f"/instances/{instance_uuid}/notifications/templates",
            json=notification_payload,
            headers=headers,
        )
        assert noti_resp.status_code == 201
        notification_template_uuid = noti_resp.json()["uuid"]

        # 3. СОЗДАЕМ ВАЛИДНЫЙ ТРИГГЕР (Проверка успешного Integrity)
        # ==========================================================
        # Настраиваем граф AST точно по твоей рабочей схеме:
        valid_trigger_payload = {
            "name": "Триггер на VIP статус",
            "trigger_type": "AUTOMATION",
            "source_template_uuid": client_template_uuid,
            "target_template_uuid": client_template_uuid,
            "event_type": "ON_RECORD_UPDATE",
            "condition_ast": {
                "type": "binary_op",
                "operator": "gt",
                "left": {"type": "field", "value": "orders_count"},
                "right": {"type": "literal", "value": 3},
            },
            "payload_ast": {"type": "field", "value": "full_name"},
            "action_name": "SEND_NOTIFICATION",
            "action_params": {
                "notification_template_uuid": notification_template_uuid,
            },
        }

        valid_trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=valid_trigger_payload,
            headers=headers,
        )

        assert valid_trigger_resp.status_code == 201

        # ==========================================================
        # 4. НЕВАЛИДНЫЙ КЕЙС: ОБРАЩЕНИЕ К НЕСУЩЕСТВУЮЩЕМУ ПОЛЮ
        # ==========================================================
        invalid_trigger_payload = {
            "name": "Сломанный триггер",
            "trigger_type": "AUTOMATION",
            "source_template_uuid": client_template_uuid,
            "target_template_uuid": client_template_uuid,
            "event_type": "ON_RECORD_UPDATE",
            "condition_ast": {
                "type": "binary_op",
                "operator": "gt",
                "left": {
                    "type": "field",
                    "value": "broken_field_does_not_exist",  # Ломаем существующее поле!
                },
                "right": {"type": "literal", "value": 10},
            },
            "payload_ast": {"type": "field", "value": "full_name"},
            "action_name": "SEND_NOTIFICATION",
            "action_params": {
                "notification_template_uuid": notification_template_uuid,
            },
        }

        invalid_trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=invalid_trigger_payload,
            headers=headers,
        )

        assert invalid_trigger_resp.status_code in [400, 422]
        assert any(
            word in invalid_trigger_resp.text.lower()
            for word in [
                "not found",
                "integrity",
                "schema_validation_error",
                "does not exist",
            ]
        )

    @pytest.mark.asyncio
    async def test_notification_template_validates_variables_against_crm_schema(
        self, test_client, create_test_environment
    ):
        """
        При сохранении шаблона уведомления сервер парсит {{...}} и проверяет поля
        по схеме привязанной CRM-таблицы в Mongo. Валидное поле сохраняется,
        несуществующее поле возвращает 400 до записи в Postgres.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        crm_template_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json={
                "name": "Клиенты для уведомлений",
                "schema": {
                    "full_name": {"type": "string", "required": True},
                    "email": {"type": "string", "required": False},
                },
            },
            headers=headers,
        )
        assert crm_template_resp.status_code == 201, crm_template_resp.text
        crm_template_uuid = crm_template_resp.json()["_id"]

        valid_resp = await test_client.post(
            f"/instances/{instance_uuid}/notifications/templates",
            json={
                "name": "Валидная переменная",
                "title": "Клиент {{data.full_name}}",
                "body": "Email: {{email}}",
                "channels": ["crm"],
                "recipients_config": {"type": "static", "uuids": [user_uuid]},
                "source_template_uuid": crm_template_uuid,
            },
            headers=headers,
        )
        assert valid_resp.status_code == 201, valid_resp.text

        invalid_resp = await test_client.post(
            f"/instances/{instance_uuid}/notifications/templates",
            json={
                "name": "Сломанная переменная",
                "title": "Клиент {{data.missing_field}}",
                "body": "Поле отсутствует в CRM-схеме",
                "channels": ["crm"],
                "recipients_config": {"type": "static", "uuids": [user_uuid]},
                "source_template_uuid": crm_template_uuid,
            },
            headers=headers,
        )
        assert invalid_resp.status_code == 400, invalid_resp.text
        assert "missing_field" in invalid_resp.text

        list_resp = await test_client.get(
            f"/instances/{instance_uuid}/notifications/templates",
            headers=headers,
        )
        assert list_resp.status_code == 200
        assert all(
            item["name"] != "Сломанная переменная" for item in list_resp.json()
        )


@pytest.mark.asyncio
async def test_get_notification_templates_filter_and_sort_success(
    test_client, create_test_environment, db_session
):
    """
    Позитивный сценарий c логированием: Проверка фильтрации и сортировки шаблонов уведомлений (PostgreSQL).
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    template_names = ["Welcome Email", "Billing Alert", "Welcome SMS"]

    for name in template_names:
        db_tpl = NotificationTemplate(
            instance_uuid=instance_uuid,
            name=name,
            title=f"Subject for {name}",
            body="<p>Hello!</p>",
        )
        db_session.add(db_tpl)

    await db_session.commit()

    search_url = f"/instances/{instance_uuid}/notifications/templates?search=welcome"
    search_resp = await test_client.get(search_url, headers=headers)
    print(
        f"[DEBUG] Ответ сервера на поиск (статус {search_resp.status_code}): {search_resp.text}"
    )

    assert search_resp.status_code == 200
    search_data = search_resp.json()

    assert len(search_data) == 2, f"Ожидали 2, но сервер вернул: {search_data}"
    returned_names = [t["name"] for t in search_data]
    assert "Welcome Email" in returned_names
    assert "Welcome SMS" in returned_names
    assert "Billing Alert" not in returned_names

    # 3. ТЕСТ 2: Сортировка по возрастанию (name:asc)
    sort_asc_resp = await test_client.get(
        f"/instances/{instance_uuid}/notifications/templates?sort_by=name:asc",
        headers=headers,
    )
    assert sort_asc_resp.status_code == 200
    sort_asc_data = sort_asc_resp.json()

    assert len(sort_asc_data) == 3
    assert sort_asc_data[0]["name"] == "Billing Alert"
    assert sort_asc_data[1]["name"] == "Welcome Email"
    assert sort_asc_data[2]["name"] == "Welcome SMS"

    # 4. ТЕСТ 3: Сортировка по убыванию (name:desc)
    sort_desc_resp = await test_client.get(
        f"/instances/{instance_uuid}/notifications/templates?sort_by=name:desc",
        headers=headers,
    )
    assert sort_desc_resp.status_code == 200
    sort_desc_data = sort_desc_resp.json()

    assert len(sort_desc_data) == 3
    assert sort_desc_data[0]["name"] == "Welcome SMS"
    assert sort_desc_data[1]["name"] == "Welcome Email"
    assert sort_desc_data[2]["name"] == "Billing Alert"

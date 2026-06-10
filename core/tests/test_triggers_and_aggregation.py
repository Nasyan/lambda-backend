# core/tests/test_triggers_and_aggregation.py

import pytest


class TestTriggersAndAggregations:

    @pytest.mark.asyncio
    async def test_client_orders_count_stored_aggregation(
        self, test_client, create_test_environment
    ):
        """
        Тестирование вычисляемой колонки с формулой-агрегацией (count) по связанной таблице.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()
        base_tpl_url = f"/instances/{instance_uuid}/templates"

        # 1. Создаем базовый шаблон "Заказы"
        order_schema = {
            "client_phone": {"type": "string", "required": True},
            "item_name": {"type": "string", "required": True},
        }
        order_tpl = await test_client.post(
            base_tpl_url,
            headers=headers,
            json={"name": "Заказы", "schema": order_schema},
        )
        order_tpl_id = order_tpl.json()["_id"]
        order_notes_url = f"{base_tpl_url}/{order_tpl_id}/notes"

        # 2. Создаем шаблон "Клиенты" с узлом агрегации в AST
        agg_ast = {
            "type": "aggregation",
            "target_template_uuid": order_tpl_id,
            "filter_field": "client_phone",
            "filter_value": {"type": "field", "value": "phone"},
            "agg_function": "count",
            "agg_field": None,
        }
        client_schema = {
            "full_name": {"type": "string", "required": True},
            "phone": {"type": "string", "required": True},
            "orders_count": {"type": "formula", "required": False, "ast": agg_ast},
        }
        client_tpl = await test_client.post(
            base_tpl_url,
            headers=headers,
            json={"name": "Клиенты", "schema": client_schema},
        )
        client_tpl_id = client_tpl.json()["_id"]
        client_notes_url = f"{base_tpl_url}/{client_tpl_id}/notes"

        # 3. Создаем клиента (заказов нет -> 0)
        phone = "+375291112233"
        client_res = await test_client.post(
            client_notes_url,
            headers=headers,
            json={"data": {"full_name": "Арсений Разработчик", "phone": phone}},
        )
        assert client_res.status_code == 201
        assert client_res.json()["data"]["orders_count"] == 0
        client_id = client_res.json()["_id"]

        # 4. Генерируем 2 связанных заказа
        for item in ["Клавиатура", "Мышка"]:
            await test_client.post(
                order_notes_url,
                headers=headers,
                json={"data": {"client_phone": phone, "item_name": item}},
            )

        # 5. Триггерим пересчет через PATCH и проверяем результат агрегации
        update_res = await test_client.patch(
            f"{client_notes_url}/{client_id}",
            headers=headers,
            json={"data": {"full_name": "Арсений Программист"}},
        )
        assert update_res.status_code == 200
        assert update_res.json()["data"]["orders_count"] == 2

    @pytest.mark.asyncio
    async def test_live_trigger_autocomplete_evaluation(
        self, test_client, create_test_environment
    ):
        """
        Тестирование триггера типа LIVE_EVAL с динамическим перехватом контекста ввода (type: input)
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон "Заказы для Live"
        order_payload = {
            "name": "Заказы для Live",
            "schema": {"target_phone": {"type": "string", "required": True}},
        }
        order_tpl = await test_client.post(
            f"/instances/{instance_uuid}/templates", headers=headers, json=order_payload
        )
        order_tpl_id = order_tpl.json()["_id"]

        # 2. Наполняем данными таблицу-источник (3 записи)
        test_phone = "+375299999999"
        for _ in range(3):
            await test_client.post(
                f"/instances/{instance_uuid}/templates/{order_tpl_id}/notes",
                headers=headers,
                json={"data": {"target_phone": test_phone}},
            )

        # 3. Режиссируем и сохраняем LIVE_EVAL триггер с узлом input в Postgres
        live_ast = {
            "type": "aggregation",
            "target_template_uuid": order_tpl_id,
            "filter_field": "target_phone",
            "filter_value": {"type": "input"},
            "agg_function": "count",
            "agg_field": None,
        }
        trigger_payload = {
            "name": "Подсказка: Кол-во заказов по телефону",
            "trigger_type": "LIVE_EVAL",
            "payload_ast": live_ast,
            "source_template_uuid": order_tpl_id,
            "target_template_uuid": order_tpl_id,
        }
        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            headers=headers,
            json=trigger_payload,
        )
        assert trigger_resp.status_code == 201, trigger_resp.text
        trigger_id = trigger_resp.json()["id"]

        # 4. Вызываем на лету расчет триггера с передачей контекста ввода фронтенда
        eval_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/{trigger_id}/evaluate",
            headers=headers,
            json={"context_data": {"__input_value__": test_phone}},
        )

        assert eval_resp.status_code == 200
        res_json = eval_resp.json()
        assert res_json["status"] == "success"
        assert res_json["result"] == 3

    @pytest.mark.asyncio
    async def test_order_total_price_with_array_reduce(
        self, test_client, create_test_environment
    ):
        """
        Тестирование свертки локального массива (array_reduce) без обращения к внешним таблицам.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()
        base_tpl_url = f"/instances/{instance_uuid}/templates"

        # 1. Создаем шаблон "Продукты" и два товара
        prod_schema = {
            "title": {"type": "string", "required": True},
            "base_price": {"type": "number", "required": True},
        }
        prod_tpl = await test_client.post(
            base_tpl_url,
            headers=headers,
            json={"name": "Продукты", "schema": prod_schema},
        )
        prod_tpl_id = prod_tpl.json()["_id"]

        p_a = await test_client.post(
            f"{base_tpl_url}/{prod_tpl_id}/notes",
            headers=headers,
            json={"data": {"title": "Клавиатура", "base_price": 450.0}},
        )
        p_b = await test_client.post(
            f"{base_tpl_url}/{prod_tpl_id}/notes",
            headers=headers,
            json={"data": {"title": "Мышь", "base_price": 120.0}},
        )
        prod_a_id, prod_b_id = p_a.json()["_id"], p_b.json()["_id"]

        # 2. Создаем шаблон "Заказы с корзиной" с узлом array_reduce
        reduce_ast = {
            "type": "array_reduce",
            "array_field": "items",
            "agg_function": "sum",
            "item_expression": {
                "type": "binary_op",
                "operator": "multiply",
                "left": {"type": "field", "value": "qty"},
                "right": {"type": "field", "value": "price"},
            },
        }
        order_schema = {
            "order_number": {"type": "string", "required": True},
            "items": {
                "type": "relation_list",
                "target_template_uuid": prod_tpl_id,
                "required": True,
            },
            "total_amount": {"type": "formula", "required": False, "ast": reduce_ast},
        }
        order_tpl = await test_client.post(
            base_tpl_url,
            headers=headers,
            json={"name": "Заказы с корзиной", "schema": order_schema},
        )
        order_tpl_id = order_tpl.json()["_id"]

        # 3. Создаем запись заказа с вложенной корзиной и проверяем расчет (2*450 + 3*120 = 1260)
        order_payload = {
            "data": {
                "order_number": "ORD-2026-001",
                "items": [
                    {"target_uuid": prod_a_id, "qty": 2, "price": 450.0},
                    {"target_uuid": prod_b_id, "qty": 3, "price": 120.0},
                ],
            }
        }
        res = await test_client.post(
            f"{base_tpl_url}/{order_tpl_id}/notes", headers=headers, json=order_payload
        )

        assert res.status_code == 201
        assert res.json()["data"]["total_amount"] == 1260.0

    @pytest.mark.asyncio
    async def test_trigger_injection_updates_template_schema(
        self, test_client, create_test_environment
    ):
        """
        Проверка автоматического связывания и инжекции метаданных триггера в схему конкретной колонки шаблона.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон со списком опций (select)
        tpl_payload = {
            "name": "Orders Template",
            "schema": {
                "order_status": {
                    "type": "select",
                    "options": ["new", "paid", "shipped"],
                    "required": True,
                },
                "amount": {"type": "number", "required": False},
            },
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates", headers=headers, json=tpl_payload
        )
        template_uuid = tpl_resp.json()["_id"]

        # 2. Регистрируем AUTOMATION триггер с привязкой к конкретному полю 'order_status'
        trigger_payload = {
            "name": "Notify on Status Change",
            "trigger_type": "AUTOMATION",
            "source_template_uuid": template_uuid,
            "target_template_uuid": template_uuid,
            "target_field": "order_status",
            "event_type": "ON_RECORD_UPDATE",
            "action_name": "test_action",
            "action_params": {"required_text": "Статус оплачен"},
            "condition_ast": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "order_status"},
                "right": {"type": "literal", "value": "paid"},
            },
            "payload_ast": {"type": "field", "value": "order_status"},
        }
        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            headers=headers,
            json=trigger_payload,
        )
        assert trigger_resp.status_code == 201, trigger_resp.text
        trigger_id = trigger_resp.json()["id"]

        # 3. Запрашиваем измененный шаблон и проверяем мутацию метаданных схемы поля
        get_tpl = await test_client.get(
            f"/instances/{instance_uuid}/templates/{template_uuid}", headers=headers
        )
        assert get_tpl.status_code == 200
        schema = get_tpl.json()["schema"]

        # Проверяем успешность инжекции триггера в целевую колонку
        assert "triggers" in schema["order_status"]
        assert schema["order_status"]["triggers"][0]["trigger_id"] == trigger_id
        assert schema["order_status"]["triggers"][0]["trigger_type"] == "AUTOMATION"
        assert schema["order_status"]["triggers"][0]["event"] == "ON_RECORD_UPDATE"

        # Проверяем изолированность: в других колонках инжекции быть не должно
        assert (
            "triggers" not in schema["amount"] or len(schema["amount"]["triggers"]) == 0
        )

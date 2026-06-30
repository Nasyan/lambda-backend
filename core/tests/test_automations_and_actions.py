# core/tests/test_automations_and_actions.py

import uuid
import pytest


class TestAutomationsAndActions:

    @pytest.mark.asyncio
    async def test_automation_trigger_execution_success(
        self, test_client, create_test_environment
    ):
        """
        Проверка ручного запуска триггера AUTOMATION с фильтрацией записей по AST (amount > 100).
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон "Сделки"
        tpl = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            headers=headers,
            json={
                "name": "Сделки",
                "schema": {"amount": {"type": "number", "required": True}},
            },
        )
        tpl_id = tpl.json()["_id"]

        # 2. Наполняем данными: одна запись проходит фильтр (>100), вторая — нет
        notes_url = f"/instances/{instance_uuid}/templates/{tpl_id}/notes"
        await test_client.post(
            notes_url, headers=headers, json={"data": {"amount": 150}}
        )
        await test_client.post(
            notes_url, headers=headers, json={"data": {"amount": 50}}
        )

        # 3. Конфигурируем триггер AUTOMATION (MANUAL вызов): payload_ast = query
        #    по записям шаблона с фильтром amount > 100 (формат v2: condition_ast/
        #    payload_ast вместо устаревшего ast, source_template_uuid обязателен)
        trigger_payload = {
            "name": "Рассылка для крупных сделок",
            "trigger_type": "AUTOMATION",
            "event_type": "MANUAL",
            "source_template_uuid": tpl_id,
            "target_template_uuid": tpl_id,
            "payload_ast": {
                "type": "query",
                "target_template_uuid": tpl_id,
                "filters": [
                    {
                        "field": "amount",
                        "operator": "gt",
                        "value": {"type": "literal", "value": 100},
                    }
                ],
            },
            "action_name": "test_action",
            "action_params": {
                "required_text": "Привет, крупный клиент!",
                "send_attempts": 3,
            },
        }
        trigger_res = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            headers=headers,
            json=trigger_payload,
        )
        assert trigger_res.status_code == 201, trigger_res.text
        trigger_id = trigger_res.json().get("id") or trigger_res.json().get("_id")

        # 4. Запускаем обработку автоматизации и сверяем количество затронутых документов
        exec_res = await test_client.post(
            f"/instances/{instance_uuid}/triggers/{trigger_id}/execute", headers=headers
        )
        assert exec_res.status_code == 200

        exec_data = exec_res.json()
        assert exec_data["status"] == "success"
        details = exec_data["execution_details"]
        assert details["status"] == "success"
        action_result = details["result"]
        assert action_result["executed_records"] == 1
        assert "Привет, крупный клиент!" in action_result["logs"][0]

    @pytest.mark.asyncio
    async def test_automation_validation_failures(
        self, test_client, create_test_environment
    ):
        """
        Проверка валидации Pydantic-схем триггеров: обязательность event_type/action_name и cron_expression.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()
        target_template = str(uuid.uuid4())
        url = f"/instances/{instance_uuid}/triggers"
        dummy_ast = {"type": "literal", "value": True}

        # Кейс А: Пропуск обязательных полей event_type и action_name для AUTOMATION триггера
        bad_payload_1 = {
            "name": "Сломанный триггер 1",
            "trigger_type": "AUTOMATION",
            "payload_ast": dummy_ast,
            "source_template_uuid": target_template,
            "target_template_uuid": target_template,
        }
        resp_1 = await test_client.post(url, json=bad_payload_1, headers=headers)
        assert resp_1.status_code == 422

        # Кейс Б: Пропуск обязательного cron_expression при event_type = CRON
        bad_payload_2 = {
            "name": "Сломанный CRON",
            "trigger_type": "AUTOMATION",
            "event_type": "CRON",
            "action_name": "test_action",
            "payload_ast": dummy_ast,
            "source_template_uuid": target_template,
            "target_template_uuid": target_template,
        }
        resp_2 = await test_client.post(url, json=bad_payload_2, headers=headers)
        assert resp_2.status_code == 422

    @pytest.mark.asyncio
    async def test_automation_execution_no_matches(
        self, test_client, create_test_environment
    ):
        """
        Проверка выполнения автоматизации, когда ни один документ в базе не подходит под AST условие.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()
        base_url = f"/instances/{instance_uuid}"

        # 1. Создаем шаблон и запись со значением ниже порогового
        tpl = await test_client.post(
            f"{base_url}/templates",
            headers=headers,
            json={
                "name": "Сделки",
                "schema": {"amount": {"type": "number", "required": True}},
            },
        )
        tpl_id = tpl.json()["_id"]
        await test_client.post(
            f"{base_url}/templates/{tpl_id}/notes",
            headers=headers,
            json={"data": {"amount": 50}},
        )

        # 2. Регистрируем триггер с заведомо недостижимым фильтром выборки (amount > 1000)
        trigger_payload = {
            "name": "Недостижимый триггер",
            "trigger_type": "AUTOMATION",
            "event_type": "MANUAL",
            "source_template_uuid": tpl_id,
            "target_template_uuid": tpl_id,
            "payload_ast": {
                "type": "query",
                "target_template_uuid": tpl_id,
                "filters": [
                    {
                        "field": "amount",
                        "operator": "gt",
                        "value": {"type": "literal", "value": 1000},
                    }
                ],
            },
            "action_name": "test_action",
            "action_params": {"required_text": "Тест"},
        }
        trigger_res = await test_client.post(
            f"{base_url}/triggers", headers=headers, json=trigger_payload
        )
        assert trigger_res.status_code == 201, trigger_res.text
        trigger_id = trigger_res.json().get("id") or trigger_res.json().get("_id")

        # 3. Выполняем и сверяем пустые счетчики при успешном статусе ответа
        exec_res = await test_client.post(
            f"{base_url}/triggers/{trigger_id}/execute", headers=headers
        )
        assert exec_res.status_code == 200

        exec_data = exec_res.json()
        assert exec_data["status"] == "success"
        action_result = exec_data["execution_details"]["result"]
        assert action_result["executed_records"] == 0
        assert action_result["logs"] == []

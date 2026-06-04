# playground/tests/conftest.py

import pytest
from typing import Dict, Any, Callable, Tuple


@pytest.fixture
def template_payload_factory() -> Callable[..., Dict[str, Any]]:
    """Фабрика для генерации адаптивной полезной нагрузки шаблона (low-code таблицы)."""

    def _factory(
        name: str = "Пользователи", embedded_triggers: list = None
    ) -> Dict[str, Any]:
        return {
            "name": name,
            "schema": {
                "email": {
                    "type": "string",
                    "required": True,
                    "triggers": embedded_triggers or [],
                },
                "age": {"type": "number", "required": False},
            },
        }

    return _factory


@pytest.fixture
def create_test_template():
    """
    Аналитический хелпер для быстрого создания шаблона в тестах.
    Принимает опциональный custom_payload, чтобы можно было подсовывать встроенные триггеры.
    Возвращает кортеж: (template_uuid, payload, instance_uuid, headers)
    """

    async def _create(
        test_client,
        create_test_environment,
        name: str = "Тестовая Таблица",
        custom_payload: Dict[str, Any] = None,
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()

        payload = custom_payload or {
            "name": name,
            "schema": {
                "price": {"type": "number", "required": True},
                "status": {"type": "string", "required": False},
            },
        }

        response = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=payload,
            headers=headers,
        )
        assert response.status_code == 201
        template_uuid = response.json()["_id"]

        return template_uuid, payload, instance_uuid, headers

    return _create


@pytest.fixture
def create_test_trigger():
    """
    Фикстура-фабрика для быстрой регистрации внешнего триггера автоматизации в PostgreSQL.
    Упрощает настройку зависимостей в интеграционных тестах.
    """

    async def _create(
        test_client,
        instance_uuid: str,
        template_uuid: str,
        headers: dict,
        name: str = "Тестовый триггер",
    ):
        trigger_payload = {
            "name": name,
            "trigger_type": "AUTOMATION",
            "target_template_uuid": template_uuid,
            "target_field": "email",
            "event_type": "ON_RECORD_UPDATE",
            "action_name": "SEND_WEBHOOK",
            "action_params": {"url": "https://hooks.pravaon.by/catch"},
            "ast": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "email"},
                "right": {"type": "literal", "value": "test@example.com"},
            },
        }

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=trigger_payload,
            headers=headers,
        )
        assert response.status_code == 201
        return response.json()

    return _create

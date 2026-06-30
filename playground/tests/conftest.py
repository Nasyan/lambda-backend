# playground/tests/conftest.py

import pytest
from typing import Dict, Any, Callable, Tuple

import pytest_asyncio


def client_upsert_trigger_payload(
    orders_template_uuid: str,
    clients_template_uuid: str,
    name: str = "Авто-создание клиента при заказе",
) -> Dict[str, Any]:
    return {
        "name": name,
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_CREATE",
        "source_template_uuid": orders_template_uuid,
        "target_template_uuid": clients_template_uuid,
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
            "source_template_uuid": template_uuid,
            "target_template_uuid": template_uuid,
            "target_field": "email",
            "event_type": "ON_RECORD_UPDATE",
            "action_name": "test_action",
            "action_params": {"required_text": "schema dependency marker"},
            "condition_ast": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "email"},
                "right": {"type": "literal", "value": "test@example.com"},
            },
            "payload_ast": {"type": "field", "value": "email"},
        }

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json=trigger_payload,
            headers=headers,
        )
        assert response.status_code == 201
        return response.json()

    return _create


@pytest_asyncio.fixture
async def setup_crm_environment(test_client, create_test_environment):
    """
    Фикстура для развертывания базовой структуры CRM:
    Создает шаблоны Клиенты, Товары и Заказы (с учетом дефолтных системных UUID
    для lookup-связей и полей выбора типа select).
    Возвращает контекст окружения и UUID созданных шаблонов.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()
    templates_url = f"/instances/{instance_uuid}/templates"

    # 1. СОЗДАЕМ ШАБЛОН: КЛИЕНТЫ
    clients_payload = {
        "name": "Клиенты",
        "schema": {
            "uuid": {"type": "string", "required": False},
            "name": {"type": "string", "required": False},
            "phone": {"type": "string", "required": True, "unique": True},
            "email": {"type": "string", "required": False},
        },
    }
    clients_resp = await test_client.post(
        templates_url, json=clients_payload, headers=headers
    )
    assert clients_resp.status_code == 201
    clients_template_uuid = clients_resp.json()["_id"]

    # 2. СОЗДАЕМ ШАБЛОН: ТОВАРЫ
    products_payload = {
        "name": "Товары",
        "schema": {
            "name": {"type": "string", "required": True},
            "quantity_left": {"type": "number", "required": True},
            "color": {"type": "string", "required": False},
            "material": {"type": "string", "required": False},
            "comm": {"type": "string", "required": False},
            "cost": {"type": "number", "required": True},
        },
    }
    products_resp = await test_client.post(
        templates_url, json=products_payload, headers=headers
    )
    assert products_resp.status_code == 201
    products_template_uuid = products_resp.json()["_id"]

    # 3. СОЗДАЕМ ШАБЛОН: ЗАКАЗЫ (с привязкой к Товарам через скрытый/дефолтный uuid)
    orders_payload = {
        "name": "Заказы",
        "schema": {
            "uuid": {"type": "string", "required": False},
            "product_list": {
                "type": "relation_list",
                "required": True,
                "target_template_uuid": products_template_uuid,
            },
            "client_phone": {"type": "string", "required": False},
            "client_name": {"type": "string", "required": False},
            "adress": {"type": "string", "required": False},
            "pickup": {"type": "boolean", "required": True},
            "cost": {"type": "number", "required": True},
            "source": {
                "type": "select",
                "options": ["сайт", "магазин физический", "инстаграм", "телеграм"],
                "required": True,
            },
            "payment": {
                "type": "select",
                "options": ["картой", "наличкой"],
                "required": True,
            },
            "real_cost": {"type": "number", "required": True},
        },
    }
    orders_resp = await test_client.post(
        templates_url, json=orders_payload, headers=headers
    )
    assert orders_resp.status_code == 201
    orders_template_uuid = orders_resp.json()["_id"]

    # Возвращаем словарь со всеми необходимыми данными для тестов
    return {
        "user_uuid": user_uuid,
        "instance_uuid": instance_uuid,
        "headers": headers,
        "clients_template_uuid": clients_template_uuid,
        "products_template_uuid": products_template_uuid,
        "orders_template_uuid": orders_template_uuid,
    }


@pytest_asyncio.fixture
async def setup_crm_environment_upgrade(test_client, create_test_environment):
    """
    Разворачивает базовую структуру CRM.
    Поле 'cost' в Заказах теперь является динамической формулой (type: formula),
    которая автоматически суммирует стоимость (cost) всех товаров из product_list.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()
    templates_url = f"/instances/{instance_uuid}/templates"

    # 1. СОЗДАЕМ ШАБЛОН: КЛИЕНТЫ
    clients_payload = {
        "name": "Клиенты",
        "schema": {
            "uuid": {"type": "string", "required": False},
            "name": {"type": "string", "required": False},
            "phone": {"type": "string", "required": True, "unique": True},
            "email": {"type": "string", "required": False},
        },
    }
    clients_resp = await test_client.post(
        templates_url, json=clients_payload, headers=headers
    )
    assert clients_resp.status_code == 201
    clients_template_uuid = clients_resp.json()["_id"]

    # 2. СОЗДАЕМ ШАБЛОН: ТОВАРЫ
    products_payload = {
        "name": "Товары",
        "schema": {
            "name": {"type": "string", "required": True},
            "quantity_left": {"type": "number", "required": True},
            "color": {"type": "string", "required": False},
            "material": {"type": "string", "required": False},
            "comm": {"type": "string", "required": False},
            "cost": {"type": "number", "required": True},
        },
    }
    products_resp = await test_client.post(
        templates_url, json=products_payload, headers=headers
    )
    assert products_resp.status_code == 201
    products_template_uuid = products_resp.json()["_id"]

    # 3. СОЗДАЕМ ШАБЛОН: ЗАКАЗЫ (cost генерируется автоматически через AST)
    orders_payload = {
        "name": "Заказы",
        "schema": {
            "uuid": {"type": "string", "required": False},
            "product_list": {
                "type": "relation_list",
                "required": True,
                "target_template_uuid": products_template_uuid,
            },
            "client_phone": {"type": "string", "required": False},
            "client_name": {"type": "string", "required": False},
            "adress": {"type": "string", "required": False},
            "pickup": {"type": "boolean", "required": True},
            # ЭВОЛЮЦИЯ: Поле теперь вычисляемое
            "cost": {
                "type": "formula",
                "required": False,
                "ast": {
                    "type": "array_reduce",
                    "array_field": "product_list",
                    "agg_function": "sum",
                    "item_expression": {
                        "type": "relation_field",
                        "relation_column": "product_list",
                        "lookup_field": "target_uuid",
                        "target_field": "cost",
                    },
                },
            },
            "source": {
                "type": "select",
                "options": ["сайт", "магазин физический", "инстаграм", "телеграм"],
                "required": True,
            },
            "payment": {
                "type": "select",
                "options": ["картой", "наличкой"],
                "required": True,
            },
            "real_cost": {"type": "number", "required": True},
        },
    }
    orders_resp = await test_client.post(
        templates_url, json=orders_payload, headers=headers
    )
    assert orders_resp.status_code == 201
    orders_template_uuid = orders_resp.json()["_id"]

    return {
        "user_uuid": user_uuid,
        "instance_uuid": instance_uuid,
        "headers": headers,
        "clients_template_uuid": clients_template_uuid,
        "products_template_uuid": products_template_uuid,
        "orders_template_uuid": orders_template_uuid,
    }


@pytest_asyncio.fixture
async def setup_crm_with_automation(test_client, setup_crm_environment):
    """
    Разворачивает базовую среду CRM и сразу регистрирует триггер AUTOMATION:
    при создании Заказа автоматически делает mongo_upsert в таблицу Клиенты.
    """
    env = setup_crm_environment
    instance_uuid = env["instance_uuid"]
    headers = env["headers"]

    clients_id = env["clients_template_uuid"]
    orders_id = env["orders_template_uuid"]

    trigger_payload = client_upsert_trigger_payload(orders_id, clients_id)

    create_trigger_url = f"/instances/{instance_uuid}/triggers"
    trigger_resp = await test_client.post(
        create_trigger_url, json=trigger_payload, headers=headers
    )
    assert trigger_resp.status_code in [200, 201]

    # Возвращаем обогащенный контекст
    return env


@pytest_asyncio.fixture
async def setup_crm_with_automation_upgrade(test_client, setup_crm_environment_upgrade):
    """
    Разворачивает базовую среду CRM и сразу регистрирует триггер AUTOMATION:
    при создании Заказа автоматически делает mongo_upsert в таблицу Клиенты.
    """
    env = setup_crm_environment_upgrade
    instance_uuid = env["instance_uuid"]
    headers = env["headers"]

    clients_id = env["clients_template_uuid"]
    orders_id = env["orders_template_uuid"]

    trigger_payload = client_upsert_trigger_payload(orders_id, clients_id)

    create_trigger_url = f"/instances/{instance_uuid}/triggers"
    trigger_resp = await test_client.post(
        create_trigger_url, json=trigger_payload, headers=headers
    )
    assert trigger_resp.status_code in [200, 201]

    # Возвращаем обогащенный контекст
    return env


@pytest_asyncio.fixture
async def setup_crm_dynamic_cost_and_trigger(test_client, create_test_environment):
    """
    Разворачивает CRM:
    1. Поле 'cost' в Заказах — динамическая формула (array_reduce), считающая сумму товаров.
    2. Триггер AUTOMATION (ON_RECORD_CREATE) — делает mongo_upsert клиента по номеру телефона.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()
    templates_url = f"/instances/{instance_uuid}/templates"

    # 1. ШАБЛОН: КЛИЕНТЫ
    clients_payload = {
        "name": "Клиенты",
        "schema": {
            "name": {"type": "string", "required": False},
            "phone": {"type": "string", "required": True, "unique": True},
        },
    }
    clients_resp = await test_client.post(
        templates_url, json=clients_payload, headers=headers
    )
    clients_template_uuid = clients_resp.json()["_id"]

    # 2. ШАБЛОН: ТОВАРЫ
    products_payload = {
        "name": "Товары",
        "schema": {
            "name": {"type": "string", "required": True},
            "quantity_left": {"type": "number", "required": True},
            "cost": {"type": "number", "required": True},
        },
    }
    products_resp = await test_client.post(
        templates_url, json=products_payload, headers=headers
    )
    products_template_uuid = products_resp.json()["_id"]

    # 3. ШАБЛОН: ЗАКАЗЫ (с авто-вычислением cost)
    orders_payload = {
        "name": "Заказы",
        "schema": {
            "product_list": {
                "type": "relation_list",
                "required": True,
                "target_template_uuid": products_template_uuid,
            },
            "client_phone": {"type": "string", "required": False},
            "client_name": {"type": "string", "required": False},
            "pickup": {"type": "boolean", "required": True},
            "cost": {
                "type": "formula",
                "required": False,
                "ast": {
                    "type": "array_reduce",
                    "array_field": "product_list",
                    "agg_function": "sum",
                    "item_expression": {
                        "type": "relation_field",
                        "relation_column": "product_list",
                        "lookup_field": "target_uuid",
                        "target_field": "cost",
                    },
                },
            },
            "source": {
                "type": "select",
                "options": ["сайт", "инстаграм"],
                "required": True,
            },
            "real_cost": {"type": "number", "required": True},
        },
    }
    orders_resp = await test_client.post(
        templates_url, json=orders_payload, headers=headers
    )
    orders_template_uuid = orders_resp.json()["_id"]

    trigger_payload = client_upsert_trigger_payload(
        orders_template_uuid,
        clients_template_uuid,
    )
    await test_client.post(
        f"/instances/{instance_uuid}/triggers", json=trigger_payload, headers=headers
    )

    return {
        "instance_uuid": instance_uuid,
        "headers": headers,
        "clients_template_uuid": clients_template_uuid,
        "products_template_uuid": products_template_uuid,
        "orders_template_uuid": orders_template_uuid,
    }


# =============================================================================
# task3 ГЗ-3 Фаза 2 — локальные фабрики доменных объектов
# =============================================================================


@pytest.fixture
def record_factory():
    """Фабрика записей через публичный API: убирает сборку URL/JSON из тестов."""

    async def _create(
        test_client, instance_uuid: str, template_uuid: str, headers: dict, **data
    ):
        response = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": data},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _create


@pytest.fixture
def trigger_factory():
    """Фабрика триггеров: postит произвольный payload и возвращает ответ."""

    async def _create(
        test_client,
        instance_uuid: str,
        headers: dict,
        payload: Dict[str, Any],
        expected_status: int = 201,
    ):
        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json=payload,
            headers=headers,
        )
        assert response.status_code == expected_status, response.text
        return response

    return _create


@pytest_asyncio.fixture
async def loyalty_crm_env(test_client, create_test_environment):
    """CRM-окружение программы лояльности (task3 ГЗ-3 Фаза 3).

    Таблицы: Клиенты (phone unique, points, tier), Товары, Заказы,
    Награды (rewards) и Дозаказы (reorder_requests) — полигон для цепочек
    каскадных автоматизаций.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()
    templates_url = f"/instances/{instance_uuid}/templates"

    async def _make_template(name, schema):
        resp = await test_client.post(
            templates_url, json={"name": name, "schema": schema}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["_id"]

    clients_id = await _make_template(
        "Клиенты",
        {
            "name": {"type": "string", "required": False},
            "phone": {"type": "string", "required": True, "unique": True},
            "points": {"type": "number", "required": False},
            "tier": {"type": "string", "required": False},
        },
    )
    products_id = await _make_template(
        "Товары",
        {
            "name": {"type": "string", "required": True},
            "quantity_left": {"type": "number", "required": True},
            "cost": {"type": "number", "required": True},
        },
    )
    orders_id = await _make_template(
        "Заказы",
        {
            "product_list": {
                "type": "relation_list",
                "required": True,
                "target_template_uuid": products_id,
            },
            "client_phone": {"type": "string", "required": False},
            "client_name": {"type": "string", "required": False},
            "pickup": {"type": "boolean", "required": True},
            "cost": {"type": "number", "required": True},
            "source": {
                "type": "select",
                "options": ["сайт", "инстаграм"],
                "required": True,
            },
            "payment": {
                "type": "select",
                "options": ["картой", "наличкой"],
                "required": True,
            },
            "real_cost": {"type": "number", "required": True},
        },
    )
    rewards_id = await _make_template(
        "Награды",
        {
            "client_phone": {"type": "string", "required": True},
            "reward": {"type": "string", "required": True},
        },
    )
    reorders_id = await _make_template(
        "Дозаказы",
        {
            "product_name": {"type": "string", "required": True, "unique": True},
            "status": {"type": "string", "required": False},
        },
    )

    return {
        "user_uuid": user_uuid,
        "instance_uuid": instance_uuid,
        "headers": headers,
        "clients_template_uuid": clients_id,
        "products_template_uuid": products_id,
        "orders_template_uuid": orders_id,
        "rewards_template_uuid": rewards_id,
        "reorders_template_uuid": reorders_id,
    }

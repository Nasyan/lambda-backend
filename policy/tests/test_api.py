# policy/tests/test_api.py

import pytest
from uuid import uuid4
from urllib.parse import quote


@pytest.mark.asyncio
async def test_create_storefront_policy_success(test_client, create_test_environment):
    """
    Позитивный сценарий: Успешное создание политики витрины.
    Все переданные поля (read_mask, write_mask, read_filters) существуют в схеме шаблона.
    """
    _, instance_uuid, headers = await create_test_environment()

    # 1. Сначала создаем валидный базовый шаблон в CRM с реальными полями
    template_payload = {
        "name": "products",
        "schema": {
            "title": {"type": "string", "required": True},
            "price": {"type": "number", "required": True},
            "secret_cost": {"type": "number", "required": False},
        },
    }
    await test_client.post(
        f"/instances/{instance_uuid}/templates", json=template_payload, headers=headers
    )

    # 2. Создаем политику витрины для этого шаблона
    policy_payload = {
        "template_name": "products",
        "read_mask": ["title", "price"],  # Скрываем secret_cost
        "write_mask": ["title"],  # Клиент может прислать только title
        "read_filters": {"price": 100},  # Серверный жесткий фильтр
    }

    response = await test_client.post(
        f"/instances/{instance_uuid}/storefront-configs",
        json=policy_payload,
        headers=headers,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["template_name"] == "products"
    assert data["read_mask"] == ["title", "price"]
    assert data["write_mask"] == ["title"]
    assert data["read_filters"] == {"price": 100}
    assert "id" in data
    assert data["instance_uuid"] == instance_uuid


@pytest.mark.asyncio
async def test_create_storefront_policy_invalid_fields(
    test_client, create_test_environment
):
    """
    Негативный сценарий: SchemaIntegrityValidator должен заблокировать создание политики,
    если администратор передал несуществующие в схеме поля.
    """
    _, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон
    template_payload = {
        "name": "orders",
        "schema": {"status": {"type": "string", "required": True}},
    }
    await test_client.post(
        f"/instances/{instance_uuid}/templates", json=template_payload, headers=headers
    )

    # 2. Пытаемся добавить в маску чтения поле "hacker_field_xyz", которого нет в схеме
    bad_policy_payload = {
        "template_name": "orders",
        "read_mask": ["status", "hacker_field_xyz"],
        "write_mask": ["status"],
        "read_filters": {},
    }

    response = await test_client.post(
        f"/instances/{instance_uuid}/storefront-configs",
        json=bad_policy_payload,
        headers=headers,
    )

    # Валидатор связности должен выбросить SchemaDependencyError, превращающийся в 400
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_storefront_policy_template_not_found(
    test_client, create_test_environment
):
    """
    Негативный сценарий: Попытка создать настройки витрины для таблицы,
    которой вообще нет в базе CRM текущего инстанса.
    """
    _, instance_uuid, headers = await create_test_environment()

    policy_payload = {
        "template_name": "ghost_table",
        "read_mask": ["id"],
        "write_mask": [],
        "read_filters": {},
    }

    response = await test_client.post(
        f"/instances/{instance_uuid}/storefront-configs",
        json=policy_payload,
        headers=headers,
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_list_storefront_policies(test_client, create_test_environment):
    """
    Позитивный сценарий: Получение списка всех настроенных политик витрины для инстанса.
    """
    _, instance_uuid, headers = await create_test_environment()

    # Создаем шаблон и политику
    template_payload = {"name": "catalog", "schema": {"sku": {"type": "string"}}}
    await test_client.post(
        f"/instances/{instance_uuid}/templates", json=template_payload, headers=headers
    )

    policy_payload = {"template_name": "catalog", "read_mask": ["sku"]}
    await test_client.post(
        f"/instances/{instance_uuid}/storefront-configs",
        json=policy_payload,
        headers=headers,
    )

    # Запрашиваем список
    response = await test_client.get(
        f"/instances/{instance_uuid}/storefront-configs", headers=headers
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["template_name"] == "catalog"


@pytest.mark.asyncio
async def test_update_storefront_policy_success(test_client, create_test_environment):
    """
    Позитивный сценарий: Успешное обновление масок и фильтров существующей политики.
    """
    _, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон
    template_payload = {
        "name": "leads",
        "schema": {"email": {"type": "string"}, "phone": {"type": "string"}},
    }
    await test_client.post(
        f"/instances/{instance_uuid}/templates", json=template_payload, headers=headers
    )

    # 2. Создаем базовую политику (разрешен только email)
    policy_payload = {"template_name": "leads", "read_mask": ["email"]}
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/storefront-configs",
        json=policy_payload,
        headers=headers,
    )
    policy_id = create_resp.json()["id"]

    # 3. Обновляем политику (добавляем phone в маску чтения и записи)
    update_payload = {
        "read_mask": ["email", "phone"],
        "write_mask": ["phone"],
        "read_filters": {"email": "test@test.com"},
    }
    response = await test_client.patch(
        f"/instances/{instance_uuid}/storefront-configs/{policy_id}",
        json=update_payload,
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["read_mask"] == ["email", "phone"]
    assert data["write_mask"] == ["phone"]
    assert data["read_filters"] == {"email": "test@test.com"}


@pytest.mark.asyncio
async def test_update_storefront_policy_invalid_fields(
    test_client, create_test_environment
):
    """
    Негативный сценарий: Блокировка PATCH-запроса обновления политики,
    если новые параметры содержат несуществующие поля.
    """
    _, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон и политику
    template_payload = {"name": "feedback", "schema": {"message": {"type": "string"}}}
    await test_client.post(
        f"/instances/{instance_uuid}/templates", json=template_payload, headers=headers
    )

    policy_payload = {"template_name": "feedback", "read_mask": ["message"]}
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/storefront-configs",
        json=policy_payload,
        headers=headers,
    )
    policy_id = create_resp.json()["id"]

    # 2. Пытаемся пропихнуть битое поле в фильтры при обновлении
    bad_update_payload = {"read_filters": {"fake_field_error": "value"}}
    response = await test_client.patch(
        f"/instances/{instance_uuid}/storefront-configs/{policy_id}",
        json=bad_update_payload,
        headers=headers,
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_delete_storefront_policy_success(test_client, create_test_environment):
    """
    Позитивный сценарий: Удаление политики витрины. Ожидаем 204 No Content.
    """
    _, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон и политику
    template_payload = {"name": "posts", "schema": {"body": {"type": "string"}}}
    await test_client.post(
        f"/instances/{instance_uuid}/templates", json=template_payload, headers=headers
    )

    policy_payload = {"template_name": "posts", "read_mask": ["body"]}
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/storefront-configs",
        json=policy_payload,
        headers=headers,
    )
    policy_id = create_resp.json()["id"]

    # 2. Удаляем
    delete_resp = await test_client.delete(
        f"/instances/{instance_uuid}/storefront-configs/{policy_id}", headers=headers
    )

    assert delete_resp.status_code == 204
    assert not delete_resp.content


@pytest.mark.asyncio
async def test_delete_storefront_policy_not_found(test_client, create_test_environment):
    """
    Негативный сценарий: Попытка удалить несуществующую политику витрины (404).
    """
    _, instance_uuid, headers = await create_test_environment()
    fake_policy_id = str(uuid4())

    response = await test_client.delete(
        f"/instances/{instance_uuid}/storefront-configs/{fake_policy_id}",
        headers=headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_storefront_schema_hides_default_fields(
    test_client, create_test_environment
):
    """
    Бизнес-логика (Чтение схемы): Если для поля задан default, оно должно быть
    полностью исключено из схемы, отдаваемой на фронтенд витрины,
    даже если оно изначально было добавлено в read_mask.
    """
    # 1. Создаем тестовое окружение
    # Фикстура возвращает строковый UUID пользователя, UUID инстанса и заголовки
    _, instance_uuid, headers = await create_test_environment()
    instance_str = str(instance_uuid)

    # 2. Воссоздаем ТОЧНЫЙ title, как его сгенерировала фикстура create_test_environment:
    raw_title = f"Тестовая Компания {instance_str[:8]}"
    # Безопасно кодируем кириллицу и пробелы для URL-пути (например, "Тестовая%20Компания...")
    valid_url_title = quote(raw_title)

    # 3. Создаем базовый шаблон в CRM через админку
    template_payload = {
        "name": "products_v2",
        "schema": {
            "name": {"type": "string"},
            "source": {"type": "string"},
        },
    }
    await test_client.post(
        f"/instances/{instance_str}/templates", json=template_payload, headers=headers
    )

    # 4. Настраиваем политику витрины через админку
    policy_payload = {
        "template_name": "products_v2",
        "read_mask": ["name", "source"],
        "write_mask": ["name"],
        "defaults": {"source": "website"},
    }
    create_policy_resp = await test_client.post(
        f"/instances/{instance_str}/storefront-configs",
        json=policy_payload,
        headers=headers,
    )
    assert create_policy_resp.status_code == 201

    # 5. Имитируем запрос от публичного клиента витрины
    # Передаем заэнкоженный title, чтобы get_active_instance_uuid нашел инстанс в Postgres
    response = await test_client.get(
        f"/storefront/{valid_url_title}/products_v2/schema", headers=headers
    )

    assert response.status_code == 200
    response_data = response.json()
    schema_fields = response_data.get("fields", {})

    # Проверяем, что логика маскирования + дефолты отработала:
    assert "name" in schema_fields
    assert "source" not in schema_fields


@pytest.mark.asyncio
async def test_storefront_create_record_enforces_defaults_and_ignores_client_input(
    test_client, create_test_environment
):
    """
    Бизнес-логика (Запись): При создании записи через витрину:
    1. Переданное клиентом значение для дефолтного поля должно игнорироваться.
    2. Значение из политики должно подставиться автоматически на сервере.
    """
    # 1. Создаем тестовое окружение
    _, instance_uuid, headers = await create_test_environment()
    instance_str = str(instance_uuid)

    # 2. Точно так же воссоздаем title инстанса и экранируем его
    raw_title = f"Тестовая Компания {instance_str[:8]}"
    valid_url_title = quote(raw_title)

    # 3. Создаем схему в CRM через админку
    template_payload = {
        "name": "orders_v2",
        "schema": {
            "customer": {"type": "string"},
            "source": {"type": "string"},
        },
    }
    await test_client.post(
        f"/instances/{instance_str}/templates", json=template_payload, headers=headers
    )

    # 4. Настраиваем политику витрины с жестким дефолтом
    policy_payload = {
        "template_name": "orders_v2",
        "write_mask": ["customer", "source"],
        "read_mask": ["customer", "source"],
        "defaults": {"source": "website"},
    }
    create_policy_resp = await test_client.post(
        f"/instances/{instance_str}/storefront-configs",
        json=policy_payload,
        headers=headers,
    )
    assert create_policy_resp.status_code == 201

    # 5. Публичный клиент отправляет заказ через модель StorefrontRecordCreateRequest
    client_payload = {
        "data": {
            "customer": "Иван",
            "source": "physical_shop",  # Попытка подменить дефолт бэкенда
        }
    }

    # Стучимся на клиентский эндпоинт создания записи с валидным title
    create_resp = await test_client.post(
        f"/storefront/{valid_url_title}/orders_v2/records",
        json=client_payload,
        headers=headers,
    )

    assert create_resp.status_code in [200, 201]

    response_data = create_resp.json()
    record_data = response_data.get("data", {})

    # Проверяем, что политика перетерла ввод клиента и установила "website"
    assert record_data["customer"] == "Иван"
    assert record_data["source"] == "website"

# store/tests/test_api.py

import pytest
from uuid import uuid4
from starlette import status

from users.models import Instances
from policy.models import StorefrontPolicies


@pytest.mark.asyncio
async def test_storefront_get_schema_and_records_lifecycle(
    test_client, create_test_environment, db_session
):
    """
    Сквозной тест витрины (Storefront):
    1. Создаем окружение, шаблон в Mongo и политику в Postgres.
    2. Проверяем получение схемы через ЧПУ (отдаются только разрешенные поля).
    3. Проверяем чтение записей анонимом (применяются read_filters и read_mask).
    4. Проверяем создание записи (данные режутся по write_mask).
    """
    # 1. Готовим базовое окружение через фикстуру
    _, instance_uuid, manager_headers = await create_test_environment()

    # Делаем инстансу уникальный текстовый title для проверки ЧПУ-логики
    instance_title = f"shop_{uuid4().hex[:6]}"

    # Обновляем инстанс в базе, задавая ему понятный title
    db_instance = await db_session.get(Instances, instance_uuid)
    db_instance.title = instance_title
    await db_session.commit()

    # 2. Создаем шаблон "Продукты" через менеджерский API CRM
    schema = {
        "title": {"type": "string", "required": True},
        "price": {"type": "number", "required": False},
        "cost_price": {
            "type": "number",
            "required": False,
        },  # Себестоимость (секретное поле)
        "in_stock": {"type": "boolean", "required": False},
    }

    tpl_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "products", "schema": schema},
        headers=manager_headers,
    )
    assert tpl_resp.status_code == 201
    template_uuid = tpl_resp.json()["_id"]

    # 3. Накатываем политику безопасности для витрины в Postgres
    # Разрешаем читать: title, price, in_stock (скрываем cost_price)
    # Разрешаем писать на витрину: только title (например, форма предзаказа)
    # Жесткий фильтр: отдавать только то, что в наличии (in_stock=True)
    policy = StorefrontPolicies(
        instance_uuid=instance_uuid,
        template_name="products",
        read_filters={"in_stock": True},
        read_mask=["title", "price", "in_stock"],
        write_mask=["title"],
    )
    db_session.add(policy)
    await db_session.commit()

    # 4. Наполняем базу через внутренний CRM API менеджера (создаем 2 товара)
    # Товар 1: В наличии
    await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json={
            "data": {
                "title": "iPhone 15",
                "price": 1000,
                "cost_price": 600,
                "in_stock": True,
            }
        },
        headers=manager_headers,
    )
    # Товар 2: Нет в наличии (должен отфильтроваться на витрине)
    await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json={
            "data": {
                "title": "Out of stock item",
                "price": 500,
                "cost_price": 300,
                "in_stock": False,
            }
        },
        headers=manager_headers,
    )

    # ==========================================
    # ТЕСТИРУЕМ STOREFRONT ЭНДПОИНТЫ (БЕЗ ТОКЕНА / ГОСТЬ)
    # ==========================================

    # А. Проверка схемы витрины
    schema_resp = await test_client.get(f"/storefront/{instance_title}/products/schema")
    assert schema_resp.status_code == 200
    schema_data = schema_resp.json()["fields"]

    assert "title" in schema_data
    assert "price" in schema_data
    assert "cost_price" not in schema_data  # Сработало сокрытие метаданных!

    # Б. Проверка чтения записей (Каталог товаров с пагинацией)
    records_resp = await test_client.get(
        f"/storefront/{instance_title}/products/records",
        params={"limit": 10, "offset": 0},
    )
    assert records_resp.status_code == 200, f"Ошибка: {records_resp.text}"

    # Распаковываем пагинированный ответ витрины
    response_data = records_resp.json()
    assert response_data["limit"] == 10
    assert response_data["offset"] == 0
    assert response_data["total"] == 1  # Должен вернуться ровно 1 товар

    records_list = response_data["results"]
    assert len(records_list) == 1

    product = records_list[0]["data"]
    assert product["title"] == "iPhone 15"
    assert "cost_price" not in product  # Поле cost_price вырезано маской безопасности!

    # В. Проверка создания записи через витрину (например, клиент оставляет заявку)
    # Пытаемся подсунуть хакерские данные ("cost_price"), которые не разрешены в write_mask
    client_payload = {
        "data": {
            "title": "Заявка от покупателя",
            "cost_price": 0,  # Будет проигнорировано/срезано сервисом витрины
            "price": 99999,  # Будет проигнорировано/срезано сервисом витрины
        }
    }

    create_resp = await test_client.post(
        f"/storefront/{instance_title}/products/records", json=client_payload
    )
    assert create_resp.status_code in (200, 201)
    created_record = create_resp.json()

    # Проверяем, что в сохраненной записи осталось ТОЛЬКО то, что разрешено в write_mask
    assert created_record["data"]["title"] == "Заявка от покупателя"
    assert "cost_price" not in created_record["data"]
    assert "price" not in created_record["data"]


@pytest.mark.asyncio
async def test_storefront_invalid_instance_title_returns_404(test_client):
    """
    Проверка безопасности: если передан несуществующий title инстанса,
    система должна корректно выдать 404 Not Found, а не падать с 500.
    """
    fake_title = "non_existent_shop_title_123"

    response = await test_client.get(f"/storefront/{fake_title}/products/schema")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "магазин не найден" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_storefront_default_strict_policy(
    test_client, create_test_environment, db_session
):
    """
    Тестируем строгую политику безопасности по умолчанию (Вариант А):
    Если админ создал шаблон в CRM, но НЕ настроил StorefrontPolicies в Postgres,
    витрина должна полностью закрыть доступ к эндпоинту и вернуть 404 Not Found.
    """
    _, instance_uuid, manager_headers = await create_test_environment()
    instance_title = f"strict_shop_{uuid4().hex[:6]}"

    # Задаем title инстансу
    db_instance = await db_session.get(Instances, instance_uuid)
    db_instance.title = instance_title
    await db_session.commit()

    # Создаем шаблон, но НЕ создаем запись политики StorefrontPolicies
    schema = {"name": {"type": "string"}}
    tpl_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "secret_data", "schema": schema},
        headers=manager_headers,
    )
    template_uuid = tpl_resp.json()["_id"]

    # Создаем запись в CRM
    await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json={"data": {"name": "Конфиденциально"}},
        headers=manager_headers,
    )

    # Дергаем витрину
    response = await test_client.get(
        f"/storefront/{instance_title}/secret_data/records"
    )

    # 🔥 ТЕПЕРЬ ОЖИДАЕМ 404, так как гард-зависимостьget_active_policy заблокировала эндпоинт
    assert response.status_code == 404
    assert (
        response.json()["detail"]
        == "Ресурс не найден или не сконфигурирован для публичного доступа."
    )

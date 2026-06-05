# triggers/tests/test_api.py

import pytest
import uuid
from triggers.models import Trigger  # Импортируй твою SQLAlchemy модель триггера

pytestmark = pytest.mark.skip(
    reason="deprecated by trigger-engine-v2; superseded by playground/tests"
)


class TestTriggersArchitecture:

    @pytest.mark.asyncio
    async def test_trigger_lifecycle_by_creator(
        self, test_client, create_test_environment
    ):
        """
        Тест 1: Позитивный сквозной кейс управления триггером.
        - Создание валидного LIVE_EVAL триггера Креатором.
        - Успешное чтение списка триггеров.
        - Успешное удаление триггера.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # Имитируем наличие UUID шаблона
        target_template_uuid = str(uuid.uuid4())

        # 1. СОЗДАНИЕ: Креатор создает корректный триггер
        valid_ast = {
            "type": "aggregation",
            "target_template_uuid": target_template_uuid,
            "filter_field": "phone",
            "filter_value": {"type": "input"},
            "agg_function": "count",
            "agg_field": None,
        }

        payload = {
            "name": "Валидный триггер подсчета",
            "trigger_type": "LIVE_EVAL",
            "ast": valid_ast,
            "target_template_uuid": target_template_uuid,
        }

        create_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/", json=payload, headers=headers
        )
        assert create_resp.status_code == 201
        trigger_uuid = create_resp.json()["id"]

        # 2. ЧТЕНИЕ: Креатор запрашивает список триггеров своего инстанса
        get_resp = await test_client.get(
            f"/instances/{instance_uuid}/triggers/", headers=headers
        )
        assert get_resp.status_code == 200
        assert len(get_resp.json()) >= 1
        assert get_resp.json()[0]["id"] == trigger_uuid

        # 3. УДАЛЕНИЕ: Креатор успешно удаляет триггер
        delete_resp = await test_client.delete(
            f"/instances/{instance_uuid}/triggers/{trigger_uuid}", headers=headers
        )
        assert delete_resp.status_code == 204

    @pytest.mark.asyncio
    async def test_invalid_ast_graph_rejection(
        self, test_client, create_test_environment
    ):
        """
        Тест 3: Защита от кривого AST при создании.
        Если фронтенд или злоумышленник шлет невалидный JSON-граф в поле 'ast',
        метод parse_ast должен выбросить исключение, а эндпоинт — вернуть 422.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        broken_payload = {
            "name": "Сломанный триггер",
            "trigger_type": "LIVE_EVAL",
            "ast": {
                "type": "binary_op",
                "operator": "unknown_action",  # Несуществующий оператор сломает parse_ast
                "left": "not_a_node",
            },
            "target_template_uuid": str(uuid.uuid4()),
        }

        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=broken_payload,
            headers=headers,
        )
        assert response.status_code == 400


class TestOrderToClientAutomation:
    @pytest.mark.asyncio
    async def test_order_creation_triggers_client_upsert(
        self, test_client, crm_template_factory
    ):
        """
        Проверка работы триггера ON_RECORD_CREATE с экшеном mongo_upsert:
        создание связанных сущностей без дублирования на основе ключевого поля (phone).
        """
        # 1. Создаем шаблоны Clients и Orders через фабрику (используем общий instance_uuid)
        tpl_clients = await crm_template_factory(
            name="Clients",
            schema={"name": {"type": "string"}, "phone": {"type": "string"}},
        )
        instance_uuid = tpl_clients["instance_uuid"]
        headers = tpl_clients["headers"]

        tpl_orders = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            headers=headers,
            json={
                "name": "Orders",
                "schema": {
                    "client_name": {"type": "string"},
                    "client_phone": {"type": "string"},
                },
            },
        )
        orders_template_uuid = tpl_orders.json()["_id"]

        # 2. Создаем триггер сквозного upsert-сохранения
        trigger_payload = {
            "name": "Авто-UPSERT клиента",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "action_name": "mongo_upsert",
            "target_template_uuid": orders_template_uuid,
            "ast": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "literal", "value": 1},
                "right": {"type": "literal", "value": 1},
            },
            "action_params": {
                "target_template_uuid": tpl_clients["template_uuid"],
                "search_fields": ["phone"],
                "payload": {
                    "phone": "{{data.client_phone}}",
                    "name": "{{data.client_name}}",
                },
            },
        }
        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=trigger_payload,
            headers=headers,
        )
        assert trigger_resp.status_code == 201

        # 3. Генерируем два последовательных заказа с одинаковым телефоном, но разным именем
        for name in ["Иван", "Иван Обновленный"]:
            await test_client.post(
                f"/instances/{instance_uuid}/templates/{orders_template_uuid}/notes",
                headers=headers,
                json={"data": {"client_name": name, "client_phone": "+79990000000"}},
            )

        # 4. Проверяем пагинированный список клиентов: документ должен остаться один с обновленным именем
        get_clients_resp = await test_client.get(
            tpl_clients["base_url"], headers=headers
        )
        assert get_clients_resp.status_code == 200

        response_data = get_clients_resp.json()
        assert response_data["total"] == 1
        assert response_data["results"][0]["data"]["name"] == "Иван Обновленный"


class TestTriggerIntegrityWork:
    @pytest.mark.asyncio
    async def test_runtime_formula_error_handling(
        self, test_client, crm_template_factory
    ):
        """
        Проверка падения вычисления выражения на лету (/evaluate) при невалидных типах в AST рантайме.
        """
        # 1. Создаем реальную таблицу с плоским описанием схемы через фабрику
        tpl = await crm_template_factory(
            name="Таблица с метриками", flat_schema={"total_score": {"type": "number"}}
        )
        instance_uuid, template_uuid, headers = (
            tpl["instance_uuid"],
            tpl["template_uuid"],
            tpl["headers"],
        )

        # 2. Создаем триггер LIVE_EVAL с заведомо конфликтующим AST (умножение числового поля на строку)
        trigger_payload = {
            "name": "Тест ошибки типов в рантайме",
            "trigger_type": "LIVE_EVAL",
            "target_template_uuid": str(template_uuid),
            "ast": {
                "type": "binary_op",
                "operator": "multiply",
                "left": {"type": "field", "value": "total_score"},
                "right": {"type": "literal", "value": "не_число_а_строка"},
            },
        }
        create_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=trigger_payload,
            headers=headers,
        )
        assert create_resp.status_code == 201

        trigger_uuid = create_resp.json().get("id") or create_resp.json().get("_id")

        # 3. Вызываем летучий расчет триггера по контексту и ловим ошибку типов
        eval_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/{trigger_uuid}/evaluate",
            json={"context_data": {"total_score": 100}},
            headers=headers,
        )
        assert eval_resp.status_code == 422
        assert "message" in eval_resp.json()

    @pytest.mark.asyncio
    async def test_cross_tenant_trigger_access_blocked(
        self, test_client, create_test_environment
    ):
        """Тест 2: ЗАЩИТА МУЛЬТИТЕНАНТНОСТИ (Cross-Tenant Attack)."""
        user_A_uuid, instance_A_uuid, headers_A = await create_test_environment()
        user_B_uuid, instance_B_uuid, headers_B = await create_test_environment()

        # 🔥 ШАГ 0: Креатор А создает таблицу в плоском формате Dict (исправлено для валидатора)
        template_payload = {
            "name": "Таблица Креатора А",
            "schema_definition": {"status": {"type": "string"}},
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_A_uuid}/templates",
            json=template_payload,
            headers=headers_A,
        )

        if tpl_resp.status_code != 201:
            print(
                f"\nОшибка создания шаблона А: {tpl_resp.status_code} - {tpl_resp.text}"
            )
        assert tpl_resp.status_code == 201

        tpl_data = tpl_resp.json()
        target_template_uuid = (
            tpl_data.get("id") or tpl_data.get("uuid") or tpl_data.get("_id")
        )

        # 1. Креатор А создает триггер
        payload = {
            "name": "Секретный триггер Инстанса А",
            "trigger_type": "LIVE_EVAL",
            "ast": {"type": "field", "value": "status"},
            "target_template_uuid": str(target_template_uuid),
        }
        create_resp = await test_client.post(
            f"/instances/{instance_A_uuid}/triggers/", json=payload, headers=headers_A
        )

        if create_resp.status_code != 201:
            print(
                f"\nОшибка создания триггера А: {create_resp.status_code} - {create_resp.text}"
            )
        assert create_resp.status_code == 201

        resp_data = create_resp.json()
        trigger_A_uuid = (
            resp_data.get("id") or resp_data.get("_id") or resp_data.get("uuid")
        )

        # 2. АТАКА 1
        bad_get_resp = await test_client.get(
            f"/instances/{instance_A_uuid}/triggers/", headers=headers_B
        )
        assert bad_get_resp.status_code == 403

        # 3. АТАКА 2
        bad_delete_resp = await test_client.delete(
            f"/instances/{instance_B_uuid}/triggers/{trigger_A_uuid}", headers=headers_B
        )
        assert bad_delete_resp.status_code == 404


@pytest.mark.asyncio
async def test_get_triggers_filter_and_sort_success(
    test_client, create_test_environment, db_session
):
    """
    Позитивный сценарий: Проверка фильтрации и сортировки списков триггеров (PostgreSQL).
    - Напрямую в БД создаем 3 триггера: 'Alpha Trigger', 'Beta Trigger', 'Alpha Advanced'.
    - Проверяем регистронезависимый поиск через ILIKE.
    - Проверяем сортировку по имени (asc / desc).
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # Фейковый UUID шаблона, так как мы пишем напрямую в БД в обход валидатора целостности
    target_template_uuid = str(uuid.uuid4())

    # 1. СОЗДАЕМ ТРИГГЕРЫ НАПРЯМУЮ В ПОСТГРЕС
    # Имена подобраны для поиска по подстроке 'alpha' и сортировки по алфавиту
    trigger_names = ["Alpha Trigger", "Beta Trigger", "Alpha Advanced"]

    for name in trigger_names:
        db_trigger = Trigger(
            instance_uuid=instance_uuid,
            name=name,
            trigger_type="LIVE_EVAL",
            target_template_uuid=target_template_uuid,
            ast={"type": "field", "value": "status"},
            # Если в модели обязательны поля created_by/updated_by:
            # created_by=user_uuid,
            # updated_by=user_uuid
        )
        db_session.add(db_trigger)

    # Сбрасываем изменения в базу данных PostgreSQL
    await db_session.commit()

    # 2. ТЕСТ 1: Проверяем поиск (поиск по подстроке "alpha" в нижнем регистре)
    # Должны вернуться 'Alpha Trigger' и 'Alpha Advanced', но НЕ 'Beta Trigger'
    search_resp = await test_client.get(
        f"/instances/{instance_uuid}/triggers/?search=alpha", headers=headers
    )
    assert search_resp.status_code == 200
    search_data = search_resp.json()

    assert len(search_data) == 2
    returned_names = [t["name"] for t in search_data]
    assert "Alpha Trigger" in returned_names
    assert "Alpha Advanced" in returned_names
    assert "Beta Trigger" not in returned_names

    # 3. ТЕСТ 2: Проверяем сортировку по имени по возрастанию (name:asc)
    # Ожидаемый порядок по алфавиту: "Alpha Advanced" -> "Alpha Trigger" -> "Beta Trigger"
    sort_asc_resp = await test_client.get(
        f"/instances/{instance_uuid}/triggers/?sort_by=name:asc", headers=headers
    )
    assert sort_asc_resp.status_code == 200
    sort_asc_data = sort_asc_resp.json()

    assert len(sort_asc_data) == 3
    assert sort_asc_data[0]["name"] == "Alpha Advanced"
    assert sort_asc_data[1]["name"] == "Alpha Trigger"
    assert sort_asc_data[2]["name"] == "Beta Trigger"

    # 4. ТЕСТ 3: Проверяем сортировку по имени по убыванию (name:desc)
    # Ожидаемый порядок: "Beta Trigger" -> "Alpha Trigger" -> "Alpha Advanced"
    sort_desc_resp = await test_client.get(
        f"/instances/{instance_uuid}/triggers/?sort_by=name:desc", headers=headers
    )
    assert sort_desc_resp.status_code == 200
    sort_desc_data = sort_desc_resp.json()

    assert len(sort_desc_data) == 3
    assert sort_desc_data[0]["name"] == "Beta Trigger"
    assert sort_desc_data[1]["name"] == "Alpha Trigger"
    assert sort_desc_data[2]["name"] == "Alpha Advanced"

    # 5. ТЕСТ 4: Комбинированный сценарий (Поиск + Сортировка)
    # Ищем "alpha" с сортировкой по убыванию (name:desc)
    # Ожидаемый порядок: "Alpha Trigger" -> "Alpha Advanced"
    combined_resp = await test_client.get(
        f"/instances/{instance_uuid}/triggers/?search=alpha&sort_by=name:desc",
        headers=headers,
    )
    assert combined_resp.status_code == 200
    combined_data = combined_resp.json()

    assert len(combined_data) == 2
    assert combined_data[0]["name"] == "Alpha Trigger"
    assert combined_data[1]["name"] == "Alpha Advanced"

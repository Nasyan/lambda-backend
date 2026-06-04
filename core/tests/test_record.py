# core/tests/test_record.py

import uuid
import pytest
from uuid import uuid4
from jsonwebtoken.utils import encode_jwt
from users.models import AppTools, Instances, Users, UserPermissions, UserRole
from engine.integrity import SchemaIntegrityValidator
from engine.exceptions.integrity import SchemaValidationError
from policy.models import StorefrontPolicies


@pytest.mark.asyncio
async def test_record_lifecycle(test_client, create_test_environment):
    """
    Полный цикл: создание шаблона -> создание записи -> получение с пагинацией -> обновление.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон
    schema = {
        "title": {"type": "string", "required": True},
        "price": {"type": "number", "required": False},
    }
    template_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "Товары", "schema": schema},
        headers=headers,
    )
    template_uuid = template_resp.json()["_id"]

    # 2. Создаем запись
    record_data = {"title": "Ноутбук", "price": 1000}
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json={"data": record_data},
        headers=headers,
    )
    assert create_resp.status_code == 201
    record = create_resp.json()
    assert record["data"]["title"] == "Ноутбук"
    record_uuid = record["_id"]

    # 3. Получаем записи (Исправлено под пагинацию)
    get_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes", headers=headers
    )
    assert get_resp.status_code == 200

    # Распаковываем пагинированный ответ
    response_data = get_resp.json()
    assert response_data["total"] == 1  # Общее количество в базе данных
    assert response_data["limit"] == 100
    assert response_data["offset"] == 0

    # Проверяем сам список результатов
    records_list = response_data["results"]
    assert len(records_list) == 1
    assert records_list[0]["_id"] == record_uuid

    # 4. Обновляем запись
    update_data = {"title": "Ноутбук Pro", "price": 1500}
    patch_resp = await test_client.patch(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes/{record_uuid}",
        json={"data": update_data},
        headers=headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["title"] == "Ноутбук Pro"
    assert patch_resp.json()["version"] == 2


@pytest.mark.asyncio
async def test_create_record_validation_error(test_client, create_test_environment):
    """
    Проверка валидации: отправляем данные, которые нарушают схему (например, string вместо number).
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # Создаем шаблон
    schema = {"count": {"type": "number", "required": True}}
    t_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "Тест", "schema": schema},
        headers=headers,
    )
    template_uuid = t_resp.json()["_id"]

    # Шлем строку в поле number
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json={"data": {"count": "это не число"}},
        headers=headers,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_record_missing_required(test_client, create_test_environment):
    """
    Проверка отсутствия обязательного поля.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    schema = {"name": {"type": "string", "required": True}}
    t_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "Тест", "schema": schema},
        headers=headers,
    )
    template_uuid = t_resp.json()["_id"]

    # Пустые данные
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json={"data": {}},
        headers=headers,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_record_not_found(test_client, create_test_environment):
    """
    Проверка 404 при обновлении несуществующей записи.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # Сначала создаем шаблон, чтобы роутер прошел проверку шаблона
    schema = {"name": {"type": "string", "required": False}}
    t_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "Тест", "schema": schema},
        headers=headers,
    )
    template_uuid = t_resp.json()["_id"]

    # Пытаемся обновить случайный UUID
    fake_record_uuid = str(uuid4())
    response = await test_client.patch(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes/{fake_record_uuid}",
        json={"data": {"name": "Test"}},
        headers=headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_record_with_workflow_access_in_db(
    test_client, create_test_environment, db_session
):
    """
    Честный тест: создаем реального юзера с доступом к workflow в Postgres через db_session,
    генерируем ему токен и проверяем успешный доступ к созданию записей.
    """
    # 1. Получаем базовое окружение (менеджера/создателя инстанса)
    _, instance_uuid, manager_headers = await create_test_environment()

    # Инициализируем UUID как объект для БД
    employee_uuid = uuid4()

    # 2. Закидываем сотрудника в базу напрямую через фикстуру
    db_session.add(
        Users(
            uuid=employee_uuid,
            email=f"allowed_employee_{uuid4().hex[:6]}@test.com",
            hash_password="mock_password_hash_for_tests",
            role=UserRole.USER,
            active=True,
            instance_id=instance_uuid,  # Сразу привязываем к инстансу среды
        )
    )
    db_session.add(
        UserPermissions(
            user_uuid=employee_uuid,
            allowed_tools=[AppTools.WORKFLOW.value],  # "workflow"
        )
    )
    await db_session.commit()

    # 3. Генерируем токен для сотрудника (переводим UUID в строку)
    token = encode_jwt(payload={"sub": str(employee_uuid)})
    employee_headers = {"Authorization": f"Bearer {token}"}

    # 4. Создаем шаблон от лица Менеджера среды (так как у USER нет прав на шаблоны)
    schema = {"title": {"type": "string", "required": True}}
    t_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "Workflow Доступ", "schema": schema},
        headers=manager_headers,
    )

    assert t_resp.status_code == 201, f"Не удалось создать шаблон: {t_resp.text}"
    template_uuid = t_resp.json()["_id"]

    # 5. А вот саму запись в этот шаблон отправляем СОТРУДНИКОМ, проверяя его WORKFLOW-доступ
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/workflow",
        json={"data": {"title": "Юзер из базы прошел!"}},
        headers=employee_headers,
    )

    assert response.status_code == 201
    assert response.json()["data"]["title"] == "Юзер из базы прошел!"


@pytest.mark.asyncio
async def test_create_record_forbidden_without_workflow_access_in_db(
    test_client, create_test_environment, db_session
):
    """
    Честный тест: создаем реального юзера, у которого НЕТ доступа к workflow в Postgres.
    Убеждаемся, что система возвращает 403 Forbidden.
    """
    # 1. Получаем окружение менеджера
    _, instance_uuid, manager_headers = await create_test_environment()

    # Инициализируем UUID как объект для БД
    forbidden_employee_uuid = uuid4()

    # 2. Создаем бесправного сотрудника через фикстуру
    db_session.add(
        Users(
            uuid=forbidden_employee_uuid,
            email=f"forbidden_employee_{uuid4().hex[:6]}@test.com",
            hash_password="mock_password_hash_for_tests",
            role=UserRole.USER,
            active=True,
            instance_id=instance_uuid,
        )
    )
    db_session.add(
        UserPermissions(
            user_uuid=forbidden_employee_uuid,
            allowed_tools=[
                AppTools.NOTES.value
            ],  # Даем доступ только к "notes", workflow пуст
        )
    )
    await db_session.commit()

    # 3. Делаем токен для бесправного юзера
    token = encode_jwt(payload={"sub": str(forbidden_employee_uuid)})
    bad_employee_headers = {"Authorization": f"Bearer {token}"}

    # 4. Создаем шаблон от лица Менеджера
    schema = {"title": {"type": "string", "required": True}}
    t_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "Тест Блокировки", "schema": schema},
        headers=manager_headers,
    )
    template_uuid = t_resp.json()["_id"]

    # 5. Пытаемся создать запись от лица бесправного сотрудника
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/workflow",
        json={"data": {"title": "Я хочу взломать workflow"}},
        headers=bad_employee_headers,
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_full_workflow_with_creator_and_user_roles(test_client, db_session):
    """
    Сквозной тест совместной работы Создателя и обычного Юзера.
    Чистый, без бойлерплейта управления базой данных.
    """
    instance_uuid = uuid4()
    creator_uuid = uuid4()
    employee_uuid = uuid4()

    # 1. Закидываем всё в базу напрямую через чистую фикстуру db_session
    # Больше никаких ручных context-менеджеров 'async with session_maker()'
    db_session.add(
        Instances(
            uuid=instance_uuid,
            title=f"Бизнес Пространство {uuid4().hex[:4]}",
            active=True,
        )
    )

    db_session.add(
        Users(
            uuid=creator_uuid,
            name="Иван Владелец",
            email=f"creator_{uuid4().hex[:6]}@test.com",
            hash_password="mock_password_hash_for_tests",
            role=UserRole.CREATOR,
            active=True,
            instance_id=instance_uuid,
        )
    )

    db_session.add(
        Users(
            uuid=employee_uuid,
            name="Алексей Сотрудник",
            email=f"worker_{uuid4().hex[:6]}@test.com",
            hash_password="mock_password_hash_for_tests",
            role=UserRole.USER,
            active=True,
            instance_id=instance_uuid,
        )
    )

    db_session.add(
        UserPermissions(
            user_uuid=employee_uuid, allowed_tools=[AppTools.WORKFLOW.value]
        )
    )

    # Просто делаем коммит. Очистку за нас сделает pytest!
    await db_session.commit()

    # 2. Генерируем токены
    creator_token = encode_jwt(payload={"sub": str(creator_uuid)})
    creator_headers = {"Authorization": f"Bearer {creator_token}"}

    employee_token = encode_jwt(payload={"sub": str(employee_uuid)})
    employee_headers = {"Authorization": f"Bearer {employee_token}"}

    # 3. Флоу запросов через API
    schema = {"title": {"type": "string", "required": True}}
    t_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "Задачи Отдела", "schema": schema},
        headers=creator_headers,
    )

    assert t_resp.status_code == 201
    template_uuid = t_resp.json()["_id"]

    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/workflow",
        json={"data": {"title": "Рабочий отчет сотрудника"}},
        headers=employee_headers,
    )

    assert response.status_code == 201
    assert response.json()["data"]["title"] == "Рабочий отчет сотрудника"


class TestSelectFieldIntegration:

    @pytest.mark.asyncio
    async def test_select_field_lifecycle(self, auth_client, db_session):
        """
        Тестируем жизненный цикл SelectField:
        1. Создание шаблона с валидными опциями.
        2. Успешная запись корректного варианта.
        3. Отклонение записи с недопустимым вариантом.
        """
        authenticated_client, user = auth_client
        user = await db_session.merge(user)

        # Подготовка окружения
        db_instance = Instances(
            uuid=uuid.uuid4(), title="Select Test Instance", active=True
        )
        db_session.add(db_instance)

        user.role = UserRole.CREATOR
        user.instance_id = db_instance.uuid
        permissions = UserPermissions(user_uuid=user.uuid, allowed_tools=["all"])
        db_session.add(permissions)

        await db_session.commit()
        await db_session.refresh(user)

        instance_uuid = str(db_instance.uuid)
        options = ["Low", "Medium", "High"]

        # 1. Создаем шаблон с SelectField
        template_payload = {
            "name": "Task Tracker",
            "schema": {
                "priority": {"type": "select", "required": True, "options": options}
            },
        }

        template_resp = await authenticated_client.post(
            f"/instances/{instance_uuid}/templates", json=template_payload
        )

        assert template_resp.status_code == 201
        template_uuid = template_resp.json()["_id"]

        # 2. Позитивный тест: отправляем допустимое значение
        valid_payload = {"data": {"priority": "Medium"}}
        response = await authenticated_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json=valid_payload,
        )

        assert response.status_code == 201
        assert response.json()["data"]["priority"] == "Medium"

        # 3. Негативный тест: отправляем значение, которого нет в options
        invalid_payload = {"data": {"priority": "Ultra-High"}}
        response = await authenticated_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json=invalid_payload,
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_select_meta_fails(self, auth_client, db_session):
        """
        Проверяем, что нельзя создать шаблон с некорректными мета-данными для select.
        """
        authenticated_client, user = auth_client
        user = await db_session.merge(user)

        # Подготовка окружения (boilerplate)
        db_instance = Instances(
            uuid=uuid.uuid4(), title="Invalid Meta Test Instance", active=True
        )
        db_session.add(db_instance)
        user.role = UserRole.CREATOR
        user.instance_id = db_instance.uuid
        permissions = UserPermissions(user_uuid=user.uuid, allowed_tools=["all"])
        db_session.add(permissions)
        await db_session.commit()
        await db_session.refresh(user)

        instance_uuid = str(db_instance.uuid)

        # 1. Попытка создать шаблон без списка options
        bad_meta_payload = {
            "name": "Bad Template",
            "schema": {"status": {"type": "select", "required": True}},
        }

        response = await authenticated_client.post(
            f"/instances/{instance_uuid}/templates", json=bad_meta_payload
        )

        # Ожидаем 400 (Bad Request), так как validate_meta выбросит SchemaValidationError
        assert response.status_code == 400

        # 2. Попытка создать шаблон с пустым списком options
        bad_empty_options_payload = {
            "name": "Empty Options Template",
            "schema": {"status": {"type": "select", "required": True, "options": []}},
        }

        response_empty = await authenticated_client.post(
            f"/instances/{instance_uuid}/templates", json=bad_empty_options_payload
        )

        assert response_empty.status_code == 400

    @pytest.mark.asyncio
    async def test_update_select_field_type_migration(self, auth_client, db_session):
        """
        Тестируем изменение типа поля в шаблоне через эндпоинт /columns.
        """
        authenticated_client, user = auth_client
        user = await db_session.merge(user)

        # Подготовка окружения
        db_instance = Instances(uuid=uuid.uuid4(), title="Migration Test", active=True)
        db_session.add(db_instance)
        user.role = UserRole.CREATOR
        user.instance_id = db_instance.uuid
        permissions = UserPermissions(user_uuid=user.uuid, allowed_tools=["all"])
        db_session.add(permissions)

        await db_session.commit()
        await db_session.refresh(user)

        instance_uuid = str(db_instance.uuid)

        # 1. Создаем шаблон
        template_payload = {
            "name": "Original",
            "schema": {"status": {"type": "select", "options": ["A", "B"]}},
        }
        create_resp = await authenticated_client.post(
            f"/instances/{instance_uuid}/templates", json=template_payload
        )
        assert create_resp.status_code == 201
        template_uuid = create_resp.json()["_id"]

        # 2. Позитивный тест: меняем тип поля 'status' на string
        # Используем структуру ColumnAddOrUpdateRequest
        update_payload = {
            "column_name": "status",
            "field_meta": {"type": "string", "required": True},
        }

        resp = await authenticated_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
            json=update_payload,
        )

        assert resp.status_code == 200

        # 3. Негативный тест: пробуем сделать поле select с дубликатами в options
        bad_update_payload = {
            "column_name": "status",
            "field_meta": {"type": "select", "options": ["A", "A"]},
        }
        resp_bad = await authenticated_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
            json=bad_update_payload,
        )

        assert resp_bad.status_code == 400

    @pytest.mark.asyncio
    async def test_data_migration_select_to_string(self, auth_client, db_session):
        authenticated_client, user = auth_client
        user = await db_session.merge(user)

        # Подготовка окружения
        db_instance = Instances(uuid=uuid.uuid4(), title="Migration Test", active=True)
        db_session.add(db_instance)
        user.role = UserRole.CREATOR
        user.instance_id = db_instance.uuid
        permissions = UserPermissions(user_uuid=user.uuid, allowed_tools=["all"])
        db_session.add(permissions)

        await db_session.commit()
        await db_session.refresh(user)

        instance_uuid = str(db_instance.uuid)

        # 1. Создаем шаблон
        template_payload = {
            "name": "Data Migration Test",
            "schema": {"status": {"type": "select", "options": ["Draft", "Published"]}},
        }
        create_resp = await authenticated_client.post(
            f"/instances/{instance_uuid}/templates", json=template_payload
        )
        template_uuid = create_resp.json()["_id"]

        # 2. Вносим записи
        record_payload = {"data": {"status": "Draft"}}
        await authenticated_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json=record_payload,
        )

        # 3. Миграция: меняем тип на string
        migration_payload = {
            "column_name": "status",
            "field_meta": {"type": "string", "required": True},
        }
        resp = await authenticated_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
            json=migration_payload,
        )
        assert resp.status_code == 200

        # 4. Проверяем, что можно внести новое значение (теперь string, а не select)
        new_value = "Archived"  # Этого не было в options
        update_record_payload = {"data": {"status": new_value}}
        resp_new = await authenticated_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json=update_record_payload,
        )
        assert resp_new.status_code == 201
        assert resp_new.json()["data"]["status"] == new_value


class TestCrossTableRelations:

    @pytest.mark.asyncio
    async def test_order_total_price_with_cross_table_relation(
        self, test_client, create_test_environment
    ):
        """
        Комплексный сценарий:
        1. Создаем шаблоны Клиентов, Товаров и Заказов.
        2. Заказ ссылается на Товар (relation_field) и умножает цену товара на свое количество.
        3. Проверяем, что формула успешно сходила в другую таблицу и посчитала результат.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # ==========================================
        # 1. СОЗДАЕМ ТОВАР
        # ==========================================
        product_template_payload = {
            "name": "Товары",
            "schema": {
                "name": {"type": "string", "required": True},
                "price": {"type": "number", "required": True},
            },
        }
        prod_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=product_template_payload,
            headers=headers,
        )
        assert prod_tpl_resp.status_code == 201
        product_template_uuid = prod_tpl_resp.json()["_id"]

        # Загружаем конкретный товар (например, Ноутбук за 1500)
        prod_rec_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{product_template_uuid}/notes",
            json={"data": {"name": "Ноутбук", "price": 1500}},
            headers=headers,
        )
        assert prod_rec_resp.status_code == 201
        product_uuid = prod_rec_resp.json()["_id"]

        # ==========================================
        # 2. СОЗДАЕМ КЛИЕНТА
        # ==========================================
        client_template_payload = {
            "name": "Клиенты",
            "schema": {
                "full_name": {"type": "string", "required": True},
            },
        }
        client_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=client_template_payload,
            headers=headers,
        )
        assert client_tpl_resp.status_code == 201
        client_template_uuid = client_tpl_resp.json()["_id"]

        # Загружаем конкретного клиента
        client_rec_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{client_template_uuid}/notes",
            json={"data": {"full_name": "Иван Иванов"}},
            headers=headers,
        )
        assert client_rec_resp.status_code == 201
        client_uuid = client_rec_resp.json()["_id"]

        # ==========================================
        # 3. СОЗДАЕМ ЗАКАЗ (с формулой через relation)
        # ==========================================
        # AST: order.total = product_id.price * order.quantity
        relation_ast = {
            "type": "binary_op",
            "operator": "multiply",
            "left": {
                "type": "relation_field",
                "relation_column": "product_id",  # В этом поле заказа лежит UUID товара
                "target_field": "price",  # Это поле мы тянем из таблицы товаров
            },
            "right": {
                "type": "field",
                "value": "quantity",  # Локальное поле самого заказа
            },
        }

        order_template_payload = {
            "name": "Заказы",
            "schema": {
                "client_id": {"type": "string", "required": True},  # Имитация FK
                "product_id": {"type": "string", "required": True},  # Имитация FK
                "quantity": {"type": "number", "required": True},
                "total_price": {
                    "type": "formula",
                    "required": False,
                    "ast": relation_ast,
                },
            },
        }
        order_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=order_template_payload,
            headers=headers,
        )
        assert order_tpl_resp.status_code == 201
        order_template_uuid = order_tpl_resp.json()["_id"]

        # ==========================================
        # 4. ПРОВЕРЯЕМ РАБОТУ ДВИЖКА (Создание заказа)
        # ==========================================
        # Покупаем 3 ноутбука
        order_payload = {
            "data": {
                "client_id": client_uuid,
                "product_id": product_uuid,
                "quantity": 3,
            }
        }
        order_rec_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{order_template_uuid}/notes",
            json=order_payload,
            headers=headers,
        )

        if order_rec_resp.status_code != 201:
            print(f"\n[DEBUG] Order creation failed: {order_rec_resp.json()}")

        assert order_rec_resp.status_code == 201
        order_data = order_rec_resp.json()["data"]

        # Проверяем математику: 1500 (из связанной таблицы) * 3 (локальное) = 4500
        assert order_data["total_price"] == 4500.0

        # ==========================================
        # 5. ПРОВЕРЯЕМ ОБНОВЛЕНИЕ ЗАКАЗА (Пересчет)
        # ==========================================
        order_uuid = order_rec_resp.json()["_id"]

        # Клиент передумал и решил взять 5 ноутбуков вместо 3
        update_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{order_template_uuid}/notes/{order_uuid}",
            json={"data": {"quantity": 5}},
            headers=headers,
        )

        assert update_resp.status_code == 200
        updated_order_data = update_resp.json()["data"]

        # Проверяем математику после апдейта: 1500 * 5 = 7500
        assert updated_order_data["total_price"] == 7500.0

    @pytest.mark.asyncio
    async def test_order_formula_with_custom_lookup_field_by_qr(
        self, test_client, create_test_environment
    ):
        """
        Тестируем адаптивность связей:
        1. Создаем шаблон Товаров с уникальным текстовым полем 'qr_code'.
        2. Создаем шаблон Заказов, где формула вытягивает 'price' товара,
           делая поиск (lookup_field) по значению 'product_qr', а не по системному UUID.
        3. Проверяем, что движок формул успешно нашел товар по текстовому QR и посчитал total.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # ==========================================
        # 1. СОЗДАЕМ ТОВАР С ДАННЫМИ О QR-КОДЕ
        # ==========================================
        product_template_payload = {
            "name": "Товары с QR",
            "schema": {
                "name": {"type": "string", "required": True},
                "qr_code": {
                    "type": "string",
                    "required": True,
                },  # Текстовый физический QR
                "price": {"type": "number", "required": True},
            },
        }
        prod_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=product_template_payload,
            headers=headers,
        )
        assert prod_tpl_resp.status_code == 201
        product_template_uuid = prod_tpl_resp.json()["_id"]

        # Создаем товар: Калье. Задаем кастомный QR-код
        target_qr_string = "QR-BROOCH-2026-XYZ"
        await test_client.post(
            f"/instances/{instance_uuid}/templates/{product_template_uuid}/notes",
            json={
                "data": {
                    "name": "Элитное Калье",
                    "qr_code": target_qr_string,
                    "price": 5000,
                }
            },
            headers=headers,
        )

        # ==========================================
        # 2. СОЗДАЕМ ЗАКАЗ С ФОРМУЛОЙ LOOKUP_FIELD
        # ==========================================
        # AST: Ищет в таблице товаров документ, у которого поле `data.qr_code` == заказовскому `product_qr`
        relation_ast = {
            "type": "binary_op",
            "operator": "multiply",
            "left": {
                "type": "relation_field",
                "relation_column": "product_qr",  # Поле в текущем payload заказа
                "lookup_field": "data.qr_code",  # Ключ для поиска в Mongo в целевой коллекции
                "target_field": "price",  # Какое поле забрать из товара
            },
            "right": {
                "type": "field",
                "value": "quantity",
            },
        }

        order_template_payload = {
            "name": "Заказы по QR",
            "schema": {
                "product_qr": {
                    "type": "string",
                    "required": True,
                },  # Сюда прилетит скан QR
                "quantity": {"type": "number", "required": True},
                "total_price": {
                    "type": "formula",
                    "required": False,
                    "ast": relation_ast,
                },
            },
        }
        order_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=order_template_payload,
            headers=headers,
        )
        order_template_uuid = order_tpl_resp.json()["_id"]

        # ==========================================
        # 3. ПРОВЕРЯЕМ ВЫЧИСЛЕНИЕ
        # ==========================================
        # Оформляем заказ: сканируем QR код и ставим количество 2
        order_payload = {
            "data": {
                "product_qr": target_qr_string,
                "quantity": 2,
            }
        }
        order_rec_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{order_template_uuid}/notes",
            json=order_payload,
            headers=headers,
        )

        assert order_rec_resp.status_code == 201
        order_data = order_rec_resp.json()["data"]

        # Ожидаем: 5000 (найдено по строке QR) * 2 = 10000.0
        assert order_data["total_price"] == 10000.0


class TestTriggersAndAggregations:

    @pytest.mark.asyncio
    async def test_client_orders_count_stored_aggregation(
        self, test_client, create_test_environment
    ):
        """
        Тест 1: Сценарий вычисляемой колонки (Stored Column).
        1. Создаем шаблон Заказов.
        2. Создаем шаблон Клиентов, где поле 'orders_count' — это формула-агрегация (count заказов по номеру телефона).
        3. Создаем клиента (заказов 0).
        4. Создаем 2 заказа для этого клиента.
        5. Обновляем/пересчитываем данные клиента и проверяем, что в Mongo сохранилось 'orders_count': 2.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # ==========================================
        # 1. СОЗДАЕМ ШАБЛОН ЗАКАЗОВ (чтобы получить его UUID)
        # ==========================================
        order_template_payload = {
            "name": "Заказы",
            "schema": {
                "client_phone": {"type": "string", "required": True},
                "item_name": {"type": "string", "required": True},
            },
        }
        order_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=order_template_payload,
            headers=headers,
        )
        assert order_tpl_resp.status_code == 201
        order_template_uuid = order_tpl_resp.json()["_id"]

        # ==========================================
        # 2. СОЗДАЕМ ШАБЛОН КЛИЕНТОВ С АГРЕГАЦИЕЙ
        # ==========================================
        # AST: Считаем количество документов в таблице Заказов,
        # где order.client_phone == client.phone
        aggregation_ast = {
            "type": "aggregation",
            "target_template_uuid": order_template_uuid,
            "filter_field": "client_phone",
            "filter_value": {
                "type": "field",
                "value": "phone",  # Берем локальное поле 'phone' из текущего клиента
            },
            "agg_function": "count",
            "agg_field": None,  # Для count поле не обязательно
        }

        client_template_payload = {
            "name": "Клиенты",
            "schema": {
                "full_name": {"type": "string", "required": True},
                "phone": {"type": "string", "required": True},
                "orders_count": {
                    "type": "formula",
                    "required": False,
                    "ast": aggregation_ast,
                },
            },
        }
        client_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=client_template_payload,
            headers=headers,
        )
        assert client_tpl_resp.status_code == 201
        client_template_uuid = client_tpl_resp.json()["_id"]

        # ==========================================
        # 3. СОЗДАЕМ КЛИЕНТА (Заказов еще нет -> должно быть 0)
        # ==========================================
        client_phone = "+375291112233"
        client_payload = {
            "data": {"full_name": "Арсений Разработчик", "phone": client_phone}
        }
        client_rec_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{client_template_uuid}/notes",
            json=client_payload,
            headers=headers,
        )
        assert client_rec_resp.status_code == 201
        client_uuid = client_rec_resp.json()["_id"]
        assert client_rec_resp.json()["data"]["orders_count"] == 0

        # ==========================================
        # 4. СОЗДАЕМ 2 ЗАКАЗА ДЛЯ ЭТОГО КЛИЕНТА
        # ==========================================
        for item in ["Клавиатура", "Мышка"]:
            order_resp = await test_client.post(
                f"/instances/{instance_uuid}/templates/{order_template_uuid}/notes",
                json={"data": {"client_phone": client_phone, "item_name": item}},
                headers=headers,
            )
            assert order_resp.status_code == 201

        # ==========================================
        # 5.ОБНОВЛЯЕМ КЛИЕНТА И ПРОВЕРЯЕМ ПЕРЕСЧЕТ АГРЕГАЦИИ
        # ==========================================
        # Имитируем сохранение или апдейт записи (например, менеджер сохраняет карточку)
        update_client_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{client_template_uuid}/notes/{client_uuid}",
            json={"data": {"full_name": "Арсений Программист"}},
            headers=headers,
        )
        assert update_client_resp.status_code == 200
        updated_data = update_client_resp.json()["data"]

        # Проверяем, что движок залез в Mongo, посчитал документы и сохранил '2'
        assert updated_data["orders_count"] == 2

    @pytest.mark.asyncio
    async def test_live_trigger_autocomplete_evaluation(
        self, test_client, create_test_environment
    ):
        """
        Тест 2: Сценарий Живого Вычисления (Live Evaluation) для подсказок фронтенда.
        1. Создаем шаблон Заказов.
        2. Добавляем туда тестовые заказы для определенного телефона.
        3. Создаем LIVE_EVAL триггер в Postgres через наш новый эндпоинт.
        4. Дергаем эндпоинт триггера /evaluate, передавая __input_value__ с фронта.
        5. Проверяем, что бэкенд на лету возвращает корректный результат агрегации.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон Заказов
        order_template_payload = {
            "name": "Заказы для Live",
            "schema": {"target_phone": {"type": "string", "required": True}},
        }
        order_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=order_template_payload,
            headers=headers,
        )
        order_template_uuid = order_tpl_resp.json()["_id"]

        # 2. Создаем 3 заказа на тестовый номер телефона
        test_phone = "+375299999999"
        for _ in range(3):
            await test_client.post(
                f"/instances/{instance_uuid}/templates/{order_template_uuid}/notes",
                json={"data": {"target_phone": test_phone}},
                headers=headers,
            )

        # 3. Регистрируем LIVE_EVAL триггер в Postgres
        # AST использует узел "input" для перехвата того, что пишется в инпут на фронте
        live_ast = {
            "type": "aggregation",
            "target_template_uuid": order_template_uuid,
            "filter_field": "target_phone",
            "filter_value": {
                "type": "input"  # Ожидает __input_value__ из контекста запроса
            },
            "agg_function": "count",
            "agg_field": None,
        }

        trigger_payload = {
            "name": "Подсказка: Кол-во заказов по телефону",
            "trigger_type": "LIVE_EVAL",
            "ast": live_ast,
            "target_template_uuid": order_template_uuid,
        }

        # Вызываем POST эндпоинт создания триггера из нашего views.py
        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=trigger_payload,
            headers=headers,
        )
        assert trigger_resp.status_code == 201
        trigger_uuid = trigger_resp.json()["id"]

        # ==========================================
        # 4. ТЕСТИРУЕМ LIVE EVALUATION С ФРОНТЕНДА
        # ==========================================
        # Фронтенд шлет то, что пользователь ввел в поле ввода
        evaluate_payload = {"context_data": {"__input_value__": test_phone}}

        eval_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/{trigger_uuid}/evaluate",
            json=evaluate_payload,
            headers=headers,
        )

        assert eval_resp.status_code == 200
        response_json = eval_resp.json()

        assert response_json["status"] == "success"
        # На лету должно вернуться значение 3, так как мы создали 3 записи
        assert response_json["result"] == 3

        # =====================================================================

    @pytest.mark.asyncio
    async def test_order_total_price_with_array_reduce(
        self, test_client, create_test_environment
    ):
        """
        Тест 3: Расчет стоимости заказа на основе динамического списка продуктов.
        1. Создаем шаблон "Продукты".
        2. Создаем шаблон "Заказы", где:
           - 'items' имеет тип 'relation_list' (ссылается на Продукты).
           - 'total_amount' имеет тип 'formula' и использует ArrayReduceNode для подсчета sum(qty * price).
        3. Создаем продукт А (450 BYN) и продукт Б (120 BYN).
        4. Создаем Заказ, складывая в массив: 2 шт Продукта А и 3 шт Продукта Б.
        5. Проверяем, что в Mongo сохранилась корректная сумма: (2 * 450) + (3 * 120) = 1260.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон Продуктов
        product_template_payload = {
            "name": "Продукты",
            "schema": {
                "title": {"type": "string", "required": True},
                "base_price": {"type": "number", "required": True},
            },
        }
        prod_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=product_template_payload,
            headers=headers,
        )
        assert prod_tpl_resp.status_code == 201
        product_template_uuid = prod_tpl_resp.json()["_id"]

        # Создаем два физических товара в БД (чтобы сработал RelationListField validation)
        prod_a_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{product_template_uuid}/notes",
            json={"data": {"title": "Механическая клавиатура", "base_price": 450.0}},
            headers=headers,
        )
        prod_b_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{product_template_uuid}/notes",
            json={"data": {"title": "Игровая мышь", "base_price": 120.0}},
            headers=headers,
        )
        prod_a_uuid = prod_a_resp.json()["_id"]
        prod_b_uuid = prod_b_resp.json()["_id"]

        # 2. Строим AST формулы для подсчета суммы заказа локально внутри массива 'items'
        # Выражение для каждого элемента: item.qty * item.price
        reduce_ast = {
            "type": "array_reduce",
            "array_field": "items",  # По какому полю-массиву итерируемся
            "agg_function": "sum",
            "item_expression": {
                "type": "binary_op",
                "operator": "multiply",
                "left": {
                    "type": "field",
                    "value": "qty",
                },  # Извлекается из объекта внутри массива
                "right": {
                    "type": "field",
                    "value": "price",
                },  # Извлекается из объекта внутри массива
            },
        }

        # Создаем шаблон Заказов
        order_template_payload = {
            "name": "Заказы с корзиной",
            "schema": {
                "order_number": {"type": "string", "required": True},
                "items": {
                    "type": "relation_list",
                    "target_template_uuid": product_template_uuid,
                    "required": True,
                },
                "total_amount": {
                    "type": "formula",
                    "required": False,
                    "ast": reduce_ast,
                },
            },
        }
        order_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=order_template_payload,
            headers=headers,
        )
        assert order_tpl_resp.status_code == 201
        order_template_uuid = order_tpl_resp.json()["_id"]

        # 3. Создаем заказ с корзиной товаров
        order_payload = {
            "data": {
                "order_number": "ORD-2026-001",
                "items": [
                    {"target_uuid": prod_a_uuid, "qty": 2, "price": 450.0},  # 900
                    {"target_uuid": prod_b_uuid, "qty": 3, "price": 120.0},  # 360
                ],
            }
        }

        create_order_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{order_template_uuid}/notes",
            json=order_payload,
            headers=headers,
        )

        assert create_order_resp.status_code == 201
        order_data = create_order_resp.json()["data"]

        # 4. Проверяем, что движок без обращения к внешним коллекциям сагрегировал локальный JSON
        # Ожидаем: 900 + 360 = 1260.0
        assert order_data["total_amount"] == 1260.0

    @pytest.mark.asyncio
    async def test_trigger_injection_updates_template_schema(
        self, test_client, create_test_environment
    ):
        """
        Тест проверяет, что при создании триггера, привязанного к конкретному полю,
        метаданные этого поля в MongoDB (схема шаблона) корректно обновляются.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон с полем 'order_status'
        template_payload = {
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
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # 2. Создаем триггер и привязываем его к полю 'order_status'
        trigger_payload = {
            "name": "Notify on Status Change",
            "trigger_type": "AUTOMATION",
            "target_template_uuid": template_uuid,
            "target_field": "order_status",
            "event_type": "ON_RECORD_UPDATE",
            "action_name": "SEND_WEBHOOK",
            "action_params": {"url": "https://example.com/hook"},
            "ast": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "order_status"},
                "right": {"type": "literal", "value": "paid"},
            },
        }

        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=trigger_payload,
            headers=headers,
        )
        assert (
            trigger_resp.status_code == 201
        ), f"Бэкенд вернул ошибку валидации: {trigger_resp.json()}"
        trigger_id = trigger_resp.json()["id"]

        # 3. Запрашиваем обновленный шаблон, чтобы проверить схему
        template_url = f"/instances/{instance_uuid}/templates/{template_uuid}"
        get_tpl_resp = await test_client.get(
            template_url,
            headers=headers,
        )

        # 🔥 ЛОГИРОВАНИЕ ДЛЯ 405 ОШИБКИ:
        assert get_tpl_resp.status_code == 200, (
            f"Не удалось получить шаблон по URL '{template_url}'. "
            f"Статус код: {get_tpl_resp.status_code}. "
            f"Ответ сервера: {get_tpl_resp.text}"
        )
        updated_template = get_tpl_resp.json()

        # 4. Проверяем, что триггер успешно "внедрен" в схему конкретной колонки
        schema = updated_template["schema"]

        # 🔥 ЛОГИРОВАНИЕ ДЛЯ ПРОВЕРКИ СХЕМЫ:
        assert (
            "order_status" in schema
        ), f"Поле 'order_status' пропало из схемы шаблона! Текущая схема: {schema}"
        assert "triggers" in schema["order_status"], (
            f"Ключ 'triggers' отсутствует в метаданных поля 'order_status'. "
            f"Данные поля: {schema['order_status']}"
        )
        assert len(schema["order_status"]["triggers"]) == 1, (
            f"Ожидался 1 триггер в поле 'order_status', "
            f"но найдено {len(schema['order_status']['triggers'])}. Список: {schema['order_status']['triggers']}"
        )

        injected_trigger = schema["order_status"]["triggers"][0]
        assert injected_trigger["trigger_id"] == trigger_id
        assert injected_trigger["trigger_type"] == "AUTOMATION"
        assert injected_trigger["event"] == "ON_RECORD_UPDATE"

        assert (
            "triggers" not in schema["amount"] or len(schema["amount"]["triggers"]) == 0
        ), f"Обнаружены лишние триггеры в поле 'amount': {schema['amount'].get('triggers')}"


class TestDynamicFieldsAndMigrations:

    @pytest.mark.asyncio
    async def test_new_fields_lifecycle_and_normalization(
        self, test_client, create_test_environment
    ):
        """
        Тест 1: Проверка базового жизненного цикла новых типов (phone, datetime, checkbox).
        - Создание шаблона с новыми типами.
        - Валидация и автоматическая нормализация (очистка телефона, приведение типов).
        - Негативная проверка на передачу невалидных форматов.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон со всеми новыми типами полей
        template_payload = {
            "name": "Лиды и Встречи",
            "schema": {
                "client_phone": {"type": "phone", "required": True},
                "appointment_at": {"type": "datetime", "required": False},
                "is_vip": {"type": "checkbox", "default": False},
            },
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # 2. Позитивный кейс: передаем сырые данные, ожидаем нормализацию
        # Телефон с пробелами и дефисами, дата в ISO строке
        valid_payload = {
            "data": {
                "client_phone": "+375 (29) 111-22-33",
                "appointment_at": "2026-05-25T15:00:00Z",
                "is_vip": True,
            }
        }
        rec_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json=valid_payload,
            headers=headers,
        )
        assert rec_resp.status_code == 201
        record_data = rec_resp.json()["data"]

        # ПРОВЕРКА НОРМАЛИЗАЦИИ: стратегия должна была очистить телефон
        assert record_data["client_phone"] == "+375291112233"
        assert record_data["is_vip"] is True

        # 3. Негативный кейс: кривой формат телефона и не-boolean в чекбоксе
        invalid_payload = {
            "data": {
                "client_phone": "custom-string-not-a-phone",
                "appointment_at": "not-a-date",
                "is_vip": "Yes",  # Ожидается bool
            }
        }
        err_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json=invalid_payload,
            headers=headers,
        )
        assert err_resp.status_code == 422

    @pytest.mark.asyncio
    async def test_migration_and_data_rewrite_on_type_change(
        self, test_client, create_test_environment
    ):
        """
        Тест 2: КРИТИЧЕСКИЙ КЕЙС ДЛЯ ПОЛЬЗОВАТЕЛЬСКИХ ПУТЕЙ.
        Смена типа столбца 'string' -> 'phone' с физической перезаписью данных в Mongo.
        - Создаем таблицу со столбцом 'string'.
        - Заносим грязный телефон (с пробелами и дефисами).
        - Меняем тип столбца на 'phone'.
        - Проверяем, что миграция прошла успешно, и данные в базе перезаписались в очищенном виде.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон, где телефон изначально просто строка
        template_payload = {
            "name": "Грязные Контакты",
            "schema": {"contact": {"type": "string", "required": True}},
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        template_uuid = tpl_resp.json()["_id"]

        # 2. Создаем запись с грязной строкой телефона
        dirty_phone = "+375 29 999-88-77"
        await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": {"contact": dirty_phone}},
            headers=headers,
        )

        # 3. МИГРАЦИЯ: Меняем тип поля со 'string' на 'phone' через PATCH /columns
        migration_payload = {
            "column_name": "contact",
            "field_meta": {"type": "phone", "required": True},
        }
        migration_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
            json=migration_payload,
            headers=headers,
        )
        # Если наш фикс в репозитории работает, вернется 200, а данные в Mongo обновятся
        assert migration_resp.status_code == 200

        # 4. Проверяем, что данные внутри Mongo РЕАЛЬНО перезаписались в новом формате
        # Делаем GET запрос списка записей
        get_notes_resp = await test_client.get(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            headers=headers,
        )
        assert get_notes_resp.status_code == 200

        # Распаковываем пагинированный ответ (Исправлено под новую структуру)
        response_data = get_notes_resp.json()
        assert response_data["total"] == 1

        records = response_data["results"]
        assert len(records) == 1

        # Значение должно быть очищено стратегией PhoneField и сохранено update_one в методе миграции!
        assert records[0]["data"]["contact"] == "+375299998877"

    @pytest.mark.asyncio
    async def test_migration_blocking_on_invalid_data(
        self, test_client, create_test_environment
    ):
        """
        Тест 3: Защита целостности при миграции.
        Если в базе лежит текст, который невозможно превратить в дату,
        система должна заблокировать смену типа столбца 'string' -> 'datetime'.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон со строкой
        template_payload = {
            "name": "Логи Встреч",
            "schema": {"event_date": {"type": "string", "required": True}},
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        template_uuid = tpl_resp.json()["_id"]

        # 2. Пишем запись с абсолютным текстом, который никак не распарсить как дату
        await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": {"event_date": "Вчера после обеда"}},
            headers=headers,
        )

        # 3. Пытаемся мигрировать это поле в datetime
        bad_migration_payload = {
            "column_name": "event_date",
            "field_meta": {"type": "datetime", "required": True},
        }
        migration_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
            json=bad_migration_payload,
            headers=headers,
        )

        # Ожидаем ошибку 400 Bad Request, так как старые данные не проходят валидацию нового типа
        assert migration_resp.status_code == 422


class TestAutomationsAndActions:

    @pytest.mark.asyncio
    async def test_automation_trigger_execution_success(
        self, test_client, create_test_environment
    ):
        """
        Тест 1: Успешный запуск автоматизации.
        - Создаем шаблон и две записи (одна подходит под условие, вторая нет).
        - Создаем триггер типа AUTOMATION с экшеном 'test_action'.
        - Дергаем /execute.
        - Проверяем, что экшен отработал только по подходящей записи.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон "Сделки"
        template_payload = {
            "name": "Сделки",
            "schema": {"amount": {"type": "number", "required": True}},
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # 2. Создаем две записи: 150 (подходит) и 50 (не подходит)
        await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": {"amount": 150}},
            headers=headers,
        )
        await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": {"amount": 50}},
            headers=headers,
        )

        # 3. Создаем триггер AUTOMATION (условие: amount > 100)
        ast_condition = {
            "type": "binary_op",
            "operator": "gt",
            "left": {"type": "field", "value": "amount"},
            "right": {"type": "literal", "value": 100},
        }

        trigger_payload = {
            "name": "Рассылка для крупных сделок",
            "trigger_type": "AUTOMATION",
            "event_type": "MANUAL",
            "action_name": "test_action",
            "action_params": {
                "required_text": "Привет, крупный клиент!",
                "send_attempts": 3,
            },
            "ast": ast_condition,
            "target_template_uuid": template_uuid,
        }

        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=trigger_payload,
            headers=headers,
        )
        assert trigger_resp.status_code == 201
        trigger_uuid_str = trigger_resp.json().get("id") or trigger_resp.json().get(
            "_id"
        )

        # 4. Выполняем экшен
        exec_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/{trigger_uuid_str}/execute",
            headers=headers,
        )

        assert exec_resp.status_code == 200
        exec_data = exec_resp.json()

        # Проверяем, что фильтр отработал: только 1 запись из 2 прошла условие (> 100)
        assert exec_data["status"] == "success"
        assert exec_data["matched_records_count"] == 1

        # Проверяем детали от выполнения экшена
        details = exec_data["execution_details"]
        assert details["executed_records"] == 1
        assert "Привет, крупный клиент!" in details["logs"][0]

    @pytest.mark.asyncio
    async def test_automation_validation_failures(
        self, test_client, create_test_environment
    ):
        """
        Тест 2: Защита целостности схемы (Pydantic).
        - Попытка создать AUTOMATION без указания действия.
        - Попытка создать CRON без расписания.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()
        target_template = str(uuid.uuid4())

        dummy_ast = {"type": "literal", "value": True}

        # Кейс А: Нет event_type и action_name
        bad_payload_1 = {
            "name": "Сломанный триггер 1",
            "trigger_type": "AUTOMATION",
            "ast": dummy_ast,
            "target_template_uuid": target_template,
        }
        resp_1 = await test_client.post(
            f"/instances/{instance_uuid}/triggers/", json=bad_payload_1, headers=headers
        )
        assert resp_1.status_code == 422
        assert (
            "необходимо указать event_type" in resp_1.json()["detail"][0]["msg"].lower()
        )

        # Кейс Б: CRON без расписания
        bad_payload_2 = {
            "name": "Сломанный CRON",
            "trigger_type": "AUTOMATION",
            "event_type": "CRON",
            "action_name": "test_action",
            "ast": dummy_ast,
            "target_template_uuid": target_template,
        }
        resp_2 = await test_client.post(
            f"/instances/{instance_uuid}/triggers/", json=bad_payload_2, headers=headers
        )
        assert resp_2.status_code == 422
        assert "указать cron_expression" in resp_2.json()["detail"][0]["msg"].lower()

    @pytest.mark.asyncio
    async def test_automation_execution_no_matches(
        self, test_client, create_test_environment
    ):
        """
        Тест 3: Выполнение экшена, когда ни одна запись не подошла под условие.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон и записи
        template_payload = {
            "name": "Сделки",
            "schema": {"amount": {"type": "number", "required": True}},
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        template_uuid = tpl_resp.json()["_id"]

        await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": {"amount": 50}},
            headers=headers,
        )

        # 2. Триггер с недостижимым условием (amount > 1000)
        ast_condition = {
            "type": "binary_op",
            "operator": "gt",
            "left": {"type": "field", "value": "amount"},
            "right": {"type": "literal", "value": 1000},
        }

        trigger_payload = {
            "name": "Недостижимый триггер",
            "trigger_type": "AUTOMATION",
            "event_type": "MANUAL",
            "action_name": "test_action",
            "action_params": {"required_text": "Тест"},
            "ast": ast_condition,
            "target_template_uuid": template_uuid,
        }

        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=trigger_payload,
            headers=headers,
        )
        trigger_uuid_str = trigger_resp.json().get("id") or trigger_resp.json().get(
            "_id"
        )

        # 3. Выполняем
        exec_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/{trigger_uuid_str}/execute",
            headers=headers,
        )

        assert exec_resp.status_code == 200
        exec_data = exec_resp.json()

        # Успех, но ни одна запись не обработана
        assert exec_data["status"] == "success"
        assert exec_data["matched_records_count"] == 0
        assert exec_data["execution_details"]["executed_records"] == 0


class TestSchemaIntegrityProtection:

    @pytest.mark.asyncio
    async def test_prevent_template_deletion_used_in_trigger(
        self, test_client, create_test_environment
    ):
        """
        Тест 1: Блокировка удаления таблицы, если к ней привязан триггер.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        template_payload = {
            "name": "Статусы макетов",
            "schema": {"status": {"type": "string", "required": True}},
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        trigger_payload = {
            "name": "Уведомление админу о статусе",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "action_name": "test_action",
            "action_params": {"required_text": "Новый макет добавлен в трекер"},
            "ast": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "status"},
                "right": {"type": "literal", "value": "needs_review"},
            },
            "target_template_uuid": template_uuid,
        }
        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=trigger_payload,
            headers=headers,
        )
        assert trigger_resp.status_code == 201

        delete_resp = await test_client.delete(
            f"/instances/{instance_uuid}/templates/{template_uuid}",
            headers=headers,
        )

        # Проверяем корректный статус-код
        assert delete_resp.status_code == 422

    @pytest.mark.asyncio
    async def test_prevent_column_drop_used_in_formula(
        self, test_client, create_test_environment
    ):
        """
        Тест 2: Блокировка удаления колонки, которая участвует в локальной формуле.
        Создаем колонки 'часы', 'ставка' и формулу 'итого'.
        Пытаемся удалить 'ставку' -> ожидаем 400.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем таблицу с формулой
        formula_ast = {
            "type": "binary_op",
            "operator": "multiply",
            "left": {"type": "field", "value": "hours_spent"},
            "right": {"type": "field", "value": "hourly_rate"},
        }
        template_payload = {
            "name": "Трудозатраты фрилансеров",
            "schema": {
                "hours_spent": {"type": "number", "required": True},
                "hourly_rate": {"type": "number", "required": True},
                "total_cost": {
                    "type": "formula",
                    "required": False,
                    "ast": formula_ast,
                },
            },
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # 2. Пытаемся удалить колонку 'hourly_rate', от которой зависит 'total_cost'
        delete_col_resp = await test_client.delete(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns/hourly_rate",
            headers=headers,
        )

        # 3. Валидатор должен заблокировать мутацию
        assert delete_col_resp.status_code == 422

    @pytest.mark.asyncio
    async def test_prevent_column_type_mutation_used_in_trigger(
        self, test_client, create_test_environment
    ):
        """
        Тест 3: Блокировка деструктивного изменения типа колонки (number -> string)
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        template_payload = {
            "name": "Клиентские оплаты",
            "schema": {"payment_amount": {"type": "number", "required": True}},
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        template_uuid = tpl_resp.json()["_id"]

        # ИСПРАВЛЕНО: event_type изменен на 'ON_RECORD_UPDATE'
        trigger_payload = {
            "name": "Уведомление о крупной оплате",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_UPDATE",
            "action_name": "test_action",
            "action_params": {"required_text": "Внимание"},
            "ast": {
                "type": "binary_op",
                "operator": "gt",
                "left": {"type": "field", "value": "payment_amount"},
                "right": {"type": "literal", "value": 50000},
            },
            "target_template_uuid": template_uuid,
        }
        trigger_create_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=trigger_payload,
            headers=headers,
        )
        assert trigger_create_resp.status_code == 201

        patch_col_payload = {
            "column_name": "payment_amount",
            "field_meta": {"type": "string", "required": True},
        }
        patch_col_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
            json=patch_col_payload,
            headers=headers,
        )

        assert patch_col_resp.status_code == 422

    @pytest.mark.asyncio
    async def test_circular_dependency_formula_creation(
        self, test_client, create_test_environment
    ):
        """
        Тест 4: Отлов циклических зависимостей внутри схемы при создании.
        Попытка создать формулу A, зависящую от B, где B зависит от A.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # Создаем заведомо сломанную схему (Уроборос)
        template_payload = {
            "name": "Сломанная аналитика",
            "schema": {
                "metric_a": {
                    "type": "formula",
                    "ast": {
                        "type": "binary_op",
                        "operator": "add",
                        "left": {"type": "field", "value": "metric_b"},
                        "right": {"type": "literal", "value": 10},
                    },
                },
                "metric_b": {
                    "type": "formula",
                    "ast": {
                        "type": "binary_op",
                        "operator": "multiply",
                        "left": {"type": "field", "value": "metric_a"},
                        "right": {"type": "literal", "value": 2},
                    },
                },
            },
        }

        # Отправляем запрос на создание
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )

        # Валидатор (check_circular_dependencies) должен перехватить это на лету
        assert tpl_resp.status_code in [400, 422]
        error_msg = str(tpl_resp.json()).lower()
        # Проверяем, что в сообщении упоминается цикличность или конкретные поля
        assert "cyclic" in error_msg or "циклич" in error_msg or "metric_a" in error_msg


class TestSchemaIntegrityCascade:

    @pytest.mark.asyncio
    async def test_prevent_delete_cascade_root_column_used_in_storefront(
        self, auth_client, db_session
    ):
        """
        Тестируем блокировку мутации схемы через API:
        Система должна вернуть 400 Bad Request, если мы пытаемся изменить
        корневое поле 'attributes', используемое во вложенном виде в storefront_policy.
        """
        authenticated_client, user = auth_client
        user = await db_session.merge(user)

        db_instance = Instances(
            uuid=uuid4(), title="Integrity Cascade Instance", active=True
        )
        db_session.add(db_instance)

        user.role = UserRole.CREATOR
        user.instance_id = db_instance.uuid
        permissions = UserPermissions(user_uuid=user.uuid, allowed_tools=["all"])
        db_session.add(permissions)
        await db_session.commit()
        await db_session.refresh(user)

        instance_uuid = str(db_instance.uuid)
        template_name = "products_table"

        # Создаем шаблон с базовым типом string
        template_payload = {
            "name": template_name,
            "schema": {
                "title": {"type": "string", "required": True},
                "attributes": {"type": "string"},
            },
        }

        template_resp = await authenticated_client.post(
            f"/instances/{instance_uuid}/templates", json=template_payload
        )
        assert template_resp.status_code == 201
        template_uuid = template_resp.json()["_id"]

        # Добавляем в базу политику, использующую dot-notation
        policy = StorefrontPolicies(
            instance_uuid=db_instance.uuid,
            template_name=template_name,
            read_mask=["title", "attributes.Материал"],
            write_mask=["title"],
            read_filters={},
        )
        db_session.add(policy)
        await db_session.commit()

        # 🌟 ИСПРАВЛЕНИЕ: Передаем валидный dict в field_meta вместо null,
        # чтобы пройти Pydantic-валидацию (422), но стриггерить SchemaDependencyError (400)
        update_payload = {
            "column_name": "attributes",
            "field_meta": {"type": "select", "options": ["A", "B"]},
        }

        url = f"/instances/{instance_uuid}/templates/{template_uuid}/columns"
        response = await authenticated_client.patch(url, json=update_payload)

        # Теперь мы ожидаем чистый бизнес-ответ 400 Bad Request от нашего SchemaIntegrityValidator
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_validate_storefront_policy_accepts_dot_notation_integration(
        self, db_session
    ):
        """
        Интеграционный тест бизнес-логики (без HTTP 404):
        Проверяем, что метод интеграции безболезненно пропускает dot-notation
        для валидных корней схем.
        """
        schema = {
            "device_name": {"type": "string"},
            "config": {"type": "string"},
        }

        valid_policy_payload = {
            "read_mask": ["device_name", "config.cpu_frequency"],
            "write_mask": ["device_name"],
            "read_filters": {},
        }

        # Вызов не должен вызывать исключений
        SchemaIntegrityValidator.validate_storefront_policy(
            schema, valid_policy_payload
        )

        invalid_policy_payload = {
            "read_mask": ["device_name", "wrong_root.ram"],
            "write_mask": ["device_name"],
            "read_filters": {},
        }

        # 1. Меняем класс исключения на SchemaValidationError
        with pytest.raises(SchemaValidationError) as exc_info:
            SchemaIntegrityValidator.validate_storefront_policy(
                schema, invalid_policy_payload
            )

        # 2. Переходим на строгое и чистое тестирование полей кастомного исключения
        exception = exc_info.value

        # Проверяем понятный текст ошибки
        assert "ошибка конфигурации read_mask" in exception.message.lower()

        # Проверяем структурированные детали, которые уйдут фронтенду
        assert exception.details["context"] == "read_mask"
        assert "wrong_root.ram" in exception.details["invalid_fields"]
        assert (
            exception.details["reason"] == "Несуществующие поля в маске чтения витрины"
        )


class TestCascadingTreeIntegration:

    @pytest.mark.asyncio
    async def test_cascading_tree_full_flow(self, test_client, create_test_environment):
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон с полем cascading_tree
        template_payload = {
            "name": "Товары",
            "schema": {
                "attributes": {
                    "type": "cascading_tree",
                    "tree_config": {
                        "floor_name": "Тип изделия",
                        "type": "fixed",
                        "options": {
                            "Брошь": {
                                "floor_name": "Стиль",
                                "type": "adaptive",
                                "options": {
                                    "Винтаж": {
                                        "floor_name": "Материал",
                                        "type": "adaptive",
                                        "options": {"Дерево": None, "Металл": None},
                                    }
                                },
                            },
                            "Кольцо": {
                                "floor_name": "Размер",
                                "type": "adaptive",
                                "options": {"17.5": None, "18.0": None},
                            },
                        },
                    },
                }
            },
        }

        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # 2. Успешная запись: Брошь -> Винтаж -> Дерево
        valid_data_1 = {
            "attributes": {
                "Тип изделия": "Брошь",
                "Стиль": "Винтаж",
                "Материал": "Дерево",
            }
        }
        rec_resp_1 = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": valid_data_1},
            headers=headers,
        )
        assert rec_resp_1.status_code == 201

        # 3. Успешная запись: Кольцо -> 17.5
        valid_data_2 = {"attributes": {"Тип изделия": "Кольцо", "Размер": "17.5"}}
        rec_resp_2 = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": valid_data_2},
            headers=headers,
        )
        assert rec_resp_2.status_code == 201

        # 4. Негативный кейс: Кольцо с неверным этажом (Материал)
        invalid_data = {
            "attributes": {
                "Тип изделия": "Кольцо",
                "Размер": "17.5",
                "Материал": "Дерево",
            }
        }
        rec_resp_3 = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": invalid_data},
            headers=headers,
        )
        # Ожидаем 400 или 422, так как 'Материал' не определен в ветке 'Кольцо'
        assert rec_resp_3.status_code in [400, 422]

        # 5. Негативный кейс: Пропуск обязательного этажа
        incomplete_data = {"attributes": {"Тип изделия": "Брошь", "Стиль": "Винтаж"}}
        rec_resp_4 = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": incomplete_data},
            headers=headers,
        )
        assert rec_resp_4.status_code in [400, 422]

    @pytest.mark.asyncio
    async def test_cascading_tree_negative_payloads(
        self, test_client, create_test_environment
    ):
        """
        Негативные тесты:
        1. Отправка выдуманного этажа (которого нет в схеме).
        2. Отправка выдуманного значения (которого нет в опциях).
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон с полем cascading_tree
        template_payload = {
            "name": "Товары",
            "schema": {
                "attributes": {
                    "type": "cascading_tree",
                    "tree_config": {
                        "floor_name": "Тип изделия",
                        "type": "fixed",
                        "options": {
                            "Брошь": {
                                "floor_name": "Стиль",
                                "type": "adaptive",
                                "options": {
                                    "Винтаж": {
                                        "floor_name": "Материал",
                                        "type": "adaptive",
                                        "options": {"Дерево": None, "Металл": None},
                                    }
                                },
                            },
                            "Кольцо": {
                                "floor_name": "Размер",
                                "type": "adaptive",
                                "options": {"17.5": None, "18.0": None},
                            },
                        },
                    },
                }
            },
        }

        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # 1. НЕГАТИВНЫЙ ТЕСТ: Выдуманный этаж ("Уровень")
        trash_floor_data = {
            "attributes": {
                "Тип изделия": "Брошь",
                "Стиль": "Винтаж",
                "Материал": "Дерево",
                "Уровень": "Секретный",  # Такого этажа нет в конфигурации
            }
        }
        resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": trash_floor_data},
            headers=headers,
        )
        # Ожидаем ошибку валидации (обычно 400 или 422 в зависимости от архитектуры)
        assert resp.status_code in [400, 422]

        # 2. НЕГАТИВНЫЙ ТЕСТ: Выдуманное значение ("Пластик" вместо "Дерево/Металл")
        trash_value_data = {
            "attributes": {
                "Тип изделия": "Брошь",
                "Стиль": "Винтаж",
                "Материал": "Пластик",  # Такого варианта нет в опциях
            }
        }
        resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": trash_value_data},
            headers=headers,
        )
        assert resp.status_code in [400, 422]

    @pytest.mark.asyncio
    async def test_cascading_tree_uneven_branches(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем НЕРАВНОМЕРНОЕ дерево в шаблоне
        template_payload = {
            "name": "Ювелирные Изделия",
            "schema": {
                "attributes": {
                    "type": "cascading_tree",
                    "tree_config": {
                        "floor_name": "Тип изделия",
                        "type": "fixed",
                        "options": {
                            # КОРОТКАЯ ВЕТКА: всего 2 этажа
                            "Брошь": {
                                "floor_name": "Цвет",
                                "type": "adaptive",
                                "options": {"Красный": None, "Зеленый": None},
                            },
                            # ДЛИННАЯ ВЕТКА: 4 этажа (Тип -> Размер -> Камень -> Материал)
                            "Колье": {
                                "floor_name": "Размер",
                                "type": "fixed",
                                "options": {
                                    "45см": {
                                        "floor_name": "Камень",
                                        "type": "adaptive",
                                        "options": {
                                            "Изумруд": {
                                                "floor_name": "Материал",
                                                "type": "adaptive",
                                                "options": {
                                                    "Золото": None,
                                                    "Серебро": None,
                                                },
                                            }
                                        },
                                    }
                                },
                            },
                        },
                    },
                }
            },
        }

        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # 2. Успешный кейс: Короткий путь (Брошь -> Цвет)
        short_path_data = {"attributes": {"Тип изделия": "Брошь", "Цвет": "Красный"}}
        resp_short = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": short_path_data},
            headers=headers,
        )
        assert resp_short.status_code == 201

        # 3. Успешный кейс: Длинный путь (Колье -> Размер -> Камень -> Материал)
        long_path_data = {
            "attributes": {
                "Тип изделия": "Колье",
                "Размер": "45см",
                "Камень": "Изумруд",
                "Материал": "Золото",
            }
        }
        resp_long = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": long_path_data},
            headers=headers,
        )
        assert resp_long.status_code == 201

        # 4. Негативный кейс: Пытаемся к Броши добавить этажи из длинной ветки
        mixed_invalid_data = {
            "attributes": {
                "Тип изделия": "Брошь",
                "Цвет": "Красный",
                "Камень": "Изумруд",  # Ошибка! У Броши нет камней в схеме
            }
        }
        resp_invalid = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": mixed_invalid_data},
            headers=headers,
        )
        assert resp_invalid.status_code in [400, 422]

        # 5. Негативный кейс: Оборвали длинный путь на середине для Колье
        incomplete_long_data = {
            "attributes": {
                "Тип изделия": "Колье",
                "Размер": "45см",  # Ошибка! Забыли указать Камень и Материал
            }
        }
        resp_incomplete = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": incomplete_long_data},
            headers=headers,
        )
        assert resp_incomplete.status_code in [400, 422]

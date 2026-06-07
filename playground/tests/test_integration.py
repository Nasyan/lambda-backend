# playground/tests/test_integration.py

import bson
import pytest


class TestCoreCrmStageOne:

    @pytest.mark.asyncio
    async def test_crm_templates_successfully_configured(
        self, test_client, setup_crm_environment
    ):
        """
        Тест 1: Базовая проверка конфигурации.
        Убеждаемся, что фикстура развернула ровно 3 таблицы.
        """
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]

        templates_url = f"/instances/{instance_uuid}/templates"

        list_resp = await test_client.get(templates_url, headers=headers)
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 3

    @pytest.mark.asyncio
    async def test_order_creation_with_valid_relations_success(
        self, test_client, setup_crm_environment
    ):
        """
        Тест 2: ПОЗИТИВНЫЙ КЕЙС.
        Создаем реальные товары и привязываем их к Заказу через системные скрытые UUID (_id).
        Проверяем, что валидация 'relation_list' и 'select' полей проходит успешно.
        """
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        products_tpl_id = env["products_template_uuid"]
        orders_tpl_id = env["orders_template_uuid"]

        # 1. Создаем физические товары в коллекции "Товары"
        prod_a_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{products_tpl_id}/notes",
            json={
                "data": {
                    "name": "Кастомная клавиатура",
                    "quantity_left": 10,
                    "cost": 350.0,
                }
            },
            headers=headers,
        )
        prod_b_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{products_tpl_id}/notes",
            json={
                "data": {
                    "name": "Эргономичная мышь",
                    "quantity_left": 25,
                    "cost": 150.0,
                }
            },
            headers=headers,
        )
        assert prod_a_resp.status_code == 201
        assert prod_b_resp.status_code == 201

        prod_a_id = prod_a_resp.json()["_id"]
        prod_b_id = prod_b_resp.json()["_id"]

        # 2. Создаем заказ, ссылаясь на созданные дефолтные _id товаров
        order_payload = {
            "data": {
                "product_list": [
                    {"target_uuid": prod_a_id, "qty": 1},
                    {"target_uuid": prod_b_id, "qty": 2},
                ],
                "client_phone": "+375291112233",
                "pickup": False,
                "cost": 650.0,
                "source": "инстаграм",  # Валидный select
                "payment": "картой",  # Валидный select
                "real_cost": 650.0,
            }
        }

        create_order_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{orders_tpl_id}/notes",
            json=order_payload,
            headers=headers,
        )

        # Ожидаем успешное создание
        assert create_order_resp.status_code == 201
        assert create_order_resp.json()["data"]["source"] == "инстаграм"

    @pytest.mark.asyncio
    async def test_order_creation_fails_on_fake_product_relation(
        self, test_client, setup_crm_environment
    ):
        """
        Тест 3: НЕГАТИВНЫЙ КЕЙС (Нарушение целостности связей).
        Попытка создать заказ со случайным/несуществующим ID товара.
        Поскольку это No-code, RelationListField должен пойти в коллекцию товаров,
        не найти там запись и выдать ошибку валидации.
        """
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        orders_tpl_id = env["orders_template_uuid"]

        # Генерируем валидный ObjectId, которого гарантированно нет в базе данных
        fake_product_id = str(bson.ObjectId())

        invalid_order_payload = {
            "data": {
                "product_list": [{"target_uuid": fake_product_id, "qty": 5}],
                "client_phone": "+375290000000",
                "pickup": True,
                "cost": 1000.0,
                "source": "сайт",
                "payment": "наличкой",
                "real_cost": 1000.0,
            }
        }

        create_order_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{orders_tpl_id}/notes",
            json=invalid_order_payload,
            headers=headers,
        )

        # Система должна заблокировать создание записи из-за битой ссылки
        assert create_order_resp.status_code in [400, 422]

    @pytest.mark.asyncio
    async def test_order_creation_fails_on_invalid_select_option(
        self, test_client, setup_crm_environment
    ):
        """
        Тест 4: НЕГАТИВНЫЙ КЕЙС (Нарушение ограничений Select опций).
        Передаем корректный товар, но подсовываем в 'source' и 'payment' значения,
        которых нет в разрешенном списке options схемы.
        """
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        products_tpl_id = env["products_template_uuid"]
        orders_tpl_id = env["orders_template_uuid"]

        # Создаем один валидный товар
        prod_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{products_tpl_id}/notes",
            json={"data": {"name": "Тестовый товар", "quantity_left": 5, "cost": 10.0}},
            headers=headers,
        )
        prod_id = prod_resp.json()["_id"]

        # Кейс А: Сломанный source ("голубиная почта")
        bad_source_payload = {
            "data": {
                "product_list": [{"target_uuid": prod_id, "qty": 1}],
                "pickup": True,
                "cost": 10.0,
                "source": "голубиная почта",  # Ошибка! Нет в опциях
                "payment": "наличкой",
                "real_cost": 10.0,
            }
        }

        resp_bad_source = await test_client.post(
            f"/instances/{instance_uuid}/templates/{orders_tpl_id}/notes",
            json=bad_source_payload,
            headers=headers,
        )
        assert resp_bad_source.status_code in [400, 422]

        # Кейс Б: Сломанный payment ("крипта")
        bad_payment_payload = {
            "data": {
                "product_list": [{"target_uuid": prod_id, "qty": 1}],
                "pickup": True,
                "cost": 10.0,
                "source": "сайт",
                "payment": "крипта",  # Ошибка! Нет в опциях
                "real_cost": 10.0,
            }
        }

        resp_bad_payment = await test_client.post(
            f"/instances/{instance_uuid}/templates/{orders_tpl_id}/notes",
            json=bad_payment_payload,
            headers=headers,
        )
        assert resp_bad_payment.status_code in [400, 422]


class TestCoreCrmStageTwo:
    @pytest.mark.asyncio
    async def test_order_with_multiple_products_and_existing_client_upsert(
        self, test_client, setup_crm_with_automation
    ):
        """
        Сценарий 1: Заказ нескольких продуктов одновременно.
        Проверяем, что тип relation_list из нескольких позиций не ломает триггер,
        а повторный заказ от того же клиента обновляет его имя (upsert).
        """
        env = setup_crm_with_automation
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        clients_id = env["clients_template_uuid"]
        products_id = env["products_template_uuid"]
        orders_id = env["orders_template_uuid"]

        # 1. Создаем два разных товара в каталоге
        prod_url = f"/instances/{instance_uuid}/templates/{products_id}/notes"

        p1 = await test_client.post(
            prod_url,
            json={"data": {"name": "Кольцо", "quantity_left": 5, "cost": 100}},
            headers=headers,
        )
        p2 = await test_client.post(
            prod_url,
            json={"data": {"name": "Серьги", "quantity_left": 2, "cost": 200}},
            headers=headers,
        )

        p1_uuid = p1.json()["_id"]
        p2_uuid = p2.json()["_id"]

        # 2. Оформляем заказ СРАЗУ на два товара
        client_phone = "+375299999999"
        order_payload = {
            "data": {
                "product_list": [{"target_uuid": p1_uuid}, {"target_uuid": p2_uuid}],
                "client_phone": client_phone,
                "client_name": "Аня Первоначальная",
                "adress": "Минск",
                "pickup": True,
                "cost": 300,
                "source": "сайт",
                "payment": "картой",
                "real_cost": 300,
            }
        }

        order_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{orders_id}/notes",
            json=order_payload,
            headers=headers,
        )
        assert order_resp.status_code == 201

        # 3. Убеждаемся, что клиент создался успешно
        clients_url = f"/instances/{instance_uuid}/templates/{clients_id}/notes"
        c_list_resp = await test_client.get(clients_url, headers=headers)
        assert c_list_resp.json()["total"] == 1
        assert c_list_resp.json()["results"][0]["data"]["name"] == "Аня Первоначальная"

        # 4. Оформляем ВТОРОЙ заказ на тот же телефон, но с измененным Именем (например, уточнили фамилию)
        second_order_payload = {
            "data": {
                "product_list": [{"target_uuid": p1_uuid}],
                "client_phone": client_phone,
                "client_name": "Аня Смирнова",  # Новое имя для проверки upsert
                "adress": "Минск",
                "pickup": True,
                "cost": 100,
                "source": "инстаграм",
                "payment": "наличкой",
                "real_cost": 100,
            }
        }

        await test_client.post(
            f"/instances/{instance_uuid}/templates/{orders_id}/notes",
            json=second_order_payload,
            headers=headers,
        )

        # 5. Проверяем, что количество записей клиентов ОСТАЛОСЬ = 1 (нет дублей), но имя ОБНОВИЛОСЬ
        c_list_resp = await test_client.get(clients_url, headers=headers)
        res_data = c_list_resp.json()
        assert res_data["total"] == 1
        assert res_data["results"][0]["data"]["name"] == "Аня Смирнова"

    @pytest.mark.asyncio
    async def test_order_lifecycle_without_phone_then_with_phone(
        self, test_client, setup_crm_with_automation
    ):
        """
        Сценарий 2: Жизненный цикл «Аноним -> Авторизованный».
        Сначала создается заказ без телефона (например, быстрый заказ через сайт без CRM-карточки),
        затем создается заказ с телефоном.
        """
        env = setup_crm_with_automation
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        clients_id = env["clients_template_uuid"]
        products_id = env["products_template_uuid"]
        orders_id = env["orders_template_uuid"]

        # Создаем базовый товар
        prod_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{products_id}/notes",
            json={"data": {"name": "Подвеска", "quantity_left": 1, "cost": 50}},
            headers=headers,
        )
        product_uuid = prod_resp.json()["_id"]

        # 1. Заказ БЕЗ телефона (пустая строка)
        anonymous_order = {
            "data": {
                "product_list": [{"target_uuid": product_uuid}],
                "client_phone": "",  # Пустое поле
                "client_name": "Аноним",
                "adress": "Самовывоз",
                "pickup": True,
                "cost": 50,
                "source": "сайт",
                "payment": "наличкой",
                "real_cost": 50,
            }
        }

        resp1 = await test_client.post(
            f"/instances/{instance_uuid}/templates/{orders_id}/notes",
            json=anonymous_order,
            headers=headers,
        )
        assert resp1.status_code == 201

        # Проверяем базу клиентов: триггер по AST условию (phone > "") должен был пропустить экшен
        clients_url = f"/instances/{instance_uuid}/templates/{clients_id}/notes"
        c_list_resp = await test_client.get(clients_url, headers=headers)
        assert c_list_resp.json()["total"] == 0  # База клиентов всё еще пуста!

        # 2. Следующий заказ от нормального клиента с телефоном
        identified_order = {
            "data": {
                "product_list": [{"target_uuid": product_uuid}],
                "client_phone": "+375295555555",
                "client_name": "Иван",
                "adress": "Минск",
                "pickup": False,
                "cost": 50,
                "source": "телеграм",
                "payment": "картой",
                "real_cost": 50,
            }
        }

        resp2 = await test_client.post(
            f"/instances/{instance_uuid}/templates/{orders_id}/notes",
            json=identified_order,
            headers=headers,
        )
        assert resp2.status_code == 201

        # Теперь в базе клиентов должна появиться ровно 1 запись Ивана
        c_list_resp = await test_client.get(clients_url, headers=headers)
        res_data = c_list_resp.json()
        assert res_data["total"] == 1
        assert res_data["results"][0]["data"]["phone"] == "+375295555555"
        assert res_data["results"][0]["data"]["name"] == "Иван"

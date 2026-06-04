# analytics/tests/test_widget.py

import uuid
import pytest


class TestAnalyticsWidgetsLifecycle:

    @pytest.mark.asyncio
    async def test_widget_full_crud_lifecycle(
        self, test_client, create_test_environment
    ):
        """
        Проверяем сквозной сценарий работы с аналитикой:
        1. Создание виджета для существующего шаблона.
        2. Обновление метаданных виджета (смена имени и типа графика).
        3. Удаление виджета.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()
        fake_template_uuid = str(uuid.uuid4())

        # Конфиг осей для подсчета суммы по полю 'total_amount' в разрезе 'status'
        chart_config = {
            "axis_x": {"field": "status", "type": "categorical"},
            "axis_y": {"field": "total_amount", "aggregation": "SUM"},
        }

        # ==========================================
        # 1. ТЕСТ: СОЗДАНИЕ ВИДЖЕТА (POST)
        # ==========================================
        create_payload = {
            "name": "Выручка по статусам",
            "target_template_uuid": fake_template_uuid,
            "widget_type": "BAR",
            "chart_config": chart_config,
            "ast_filter": None,
        }

        create_resp = await test_client.post(
            f"/instances/{instance_uuid}/widgets",
            json=create_payload,
            headers=headers,
        )
        assert create_resp.status_code == 201
        widget_data = create_resp.json()
        assert widget_data["name"] == "Выручка по статусам"
        assert widget_data["widget_type"] == "BAR"
        widget_uuid = widget_data["id"]

        # ==========================================
        # 2. ТЕСТ: ОБНОВЛЕНИЕ ВИДЖЕТА (PATCH)
        # ==========================================
        update_payload = {
            "name": "Новое имя: Анализ продаж",
            "widget_type": "LINE",  # Переключили с BAR на LINE
        }

        update_resp = await test_client.patch(
            f"/instances/{instance_uuid}/widgets/{widget_uuid}",
            json=update_payload,
            headers=headers,
        )
        assert update_resp.status_code == 200
        updated_data = update_resp.json()
        assert updated_data["name"] == "Новое имя: Анализ продаж"
        assert updated_data["widget_type"] == "LINE"
        # Проверяем, что chart_config не затерся при частичном обновлении
        assert updated_data["chart_config"]["axis_x"]["field"] == "status"

        # ==========================================
        # 3. ТЕСТ: УДАЛЕНИЕ ВИДЖЕТА (DELETE)
        # ==========================================
        delete_resp = await test_client.delete(
            f"/instances/{instance_uuid}/widgets/{widget_uuid}",
            headers=headers,
        )
        assert delete_resp.status_code == 204

        # Дополнительно проверяем, что эндпоинт данных теперь отдает 404
        data_resp = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{widget_uuid}/data",
            headers=headers,
        )
        assert data_resp.status_code == 404


class TestAnalyticsWidgetsEdgeCases:

    @pytest.mark.asyncio
    async def test_create_widget_with_invalid_ast_fails(
        self, test_client, create_test_environment
    ):
        """
        КЕЙС: Попытка создать виджет с синтаксически некорректным AST-фильтром.
        Ожидаем: 400 Bad Request от Pydantic (ValidationError -> FormulaValidationError).
        """
        _, instance_uuid, headers = await create_test_environment()

        # Сломанная структура: оператор требует 'left' и 'right', а мы их не передали
        bad_ast_payload = {
            "name": "Сломанный фильтр",
            "target_template_uuid": str(uuid.uuid4()),
            "widget_type": "BAR",
            "chart_config": {
                "axis_x": {"field": "status", "type": "categorical"},
                "axis_y": {"field": "amount", "aggregation": "SUM"},
            },
            "ast_filter": {
                "type": "binary_op",
                "operator": "gt",
                # Поля 'left' и 'right' пропущены
            },
        }

        response = await test_client.post(
            f"/instances/{instance_uuid}/widgets",
            json=bad_ast_payload,
            headers=headers,
        )

        # Наш parse_ast должен отловить ValidationError и выбросить FormulaValidationError,
        # которая на уровне FastAPI превращается в 400 ошибку.
        assert response.status_code == 400
        assert "invalid formula structure" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_data_for_non_existent_widget_fails(
        self, test_client, create_test_environment
    ):
        """
        КЕЙС: Запрос данных для несуществующего widget_uuid.
        Ожидаем: 404 Not Found.
        """
        _, instance_uuid, headers = await create_test_environment()
        random_widget_uuid = str(uuid.uuid4())

        response = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{random_widget_uuid}/data",
            headers=headers,
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_widget_aggregation_with_empty_mongo_collection(
        self, test_client, create_test_environment
    ):
        """
        КЕЙС: Виджет создан корректно, но в MongoDB еще нет ни одной записи (коллекция пустая).
        Ожидаем: Успешный ответ (200 OK) со значением пустого списка [], система не должна падать.
        """
        _, instance_uuid, headers = await create_test_environment()
        empty_template_uuid = str(uuid.uuid4())

        # Создаем виджет на пустую таблицу
        create_payload = {
            "name": "Пустой график",
            "target_template_uuid": empty_template_uuid,
            "widget_type": "PIE",
            "chart_config": {
                "axis_x": {"field": "category", "type": "categorical"},
                "axis_y": {"field": "views", "aggregation": "COUNT"},
            },
            "ast_filter": None,
        }

        create_resp = await test_client.post(
            f"/instances/{instance_uuid}/widgets",
            json=create_payload,
            headers=headers,
        )
        widget_uuid = create_resp.json()["id"]

        # Запрашиваем данные
        data_resp = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{widget_uuid}/data",
            headers=headers,
        )

        assert data_resp.status_code == 200
        assert (
            data_resp.json() == []
        )  # Mongo аггрегация вернет пустой курсор -> пустой список

    @pytest.mark.asyncio
    async def test_widget_update_non_existent_fails(
        self, test_client, create_test_environment
    ):
        """
        КЕЙС: Попытка обновить настройки несуществующего виджета.
        Ожидаем: 404 Not Found.
        """
        _, instance_uuid, headers = await create_test_environment()
        random_widget_uuid = str(uuid.uuid4())

        response = await test_client.patch(
            f"/instances/{instance_uuid}/widgets/{random_widget_uuid}",
            json={"name": "Новое имя"},
            headers=headers,
        )

        assert response.status_code == 404


class TestAnalyticsFullFlow:

    @pytest.mark.asyncio
    async def test_sales_funnel_full_analytics_flow(
        self, test_client, create_test_environment
    ):
        """
        Полный E2E флоу аналитики:
        1. Создание шаблона (Сделки).
        2. Наполнение данными.
        3. Создание виджета с группировкой, суммированием и AST-фильтром.
        4. Проверка корректности агрегированных данных.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # ==========================================
        # 1. СОЗДАЕМ ШАБЛОН "СДЕЛКИ"
        # ==========================================
        template_payload = {
            "name": "Сделки",
            "schema": {
                "manager": {"type": "string", "required": True},
                "amount": {"type": "number", "required": True},
                "status": {
                    "type": "select",
                    "options": ["Won", "Lost", "Draft"],
                    "required": True,
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

        # ==========================================
        # 2. НАПОЛНЯЕМ БАЗУ ДАННЫМИ (MongoDB)
        # ==========================================
        # Менеджер Алиса (Успешно на 3000, Провал на 500)
        # Менеджер Боб (Успешно на 1500)
        mock_deals = [
            {"manager": "Alice", "amount": 1000, "status": "Won"},
            {"manager": "Alice", "amount": 2000, "status": "Won"},
            {
                "manager": "Alice",
                "amount": 500,
                "status": "Lost",
            },  # Должно отфильтроваться
            {"manager": "Bob", "amount": 1500, "status": "Won"},
            {
                "manager": "Bob",
                "amount": 100,
                "status": "Draft",
            },  # Должно отфильтроваться
        ]

        for deal in mock_deals:
            rec_resp = await test_client.post(
                f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
                json={"data": deal},
                headers=headers,
            )
            assert rec_resp.status_code == 201

        # ==========================================
        # 3. СОЗДАЕМ ВИДЖЕТ АНАЛИТИКИ
        # ==========================================
        # Цель: Построить график суммарной выручки по менеджерам, но ТОЛЬКО для status == "Won"
        widget_payload = {
            "name": "Выручка по менеджерам",
            "target_template_uuid": template_uuid,
            "widget_type": "BAR",
            "chart_config": {
                "axis_x": {"field": "manager", "type": "categorical"},
                "axis_y": {"field": "amount", "aggregation": "SUM"},
            },
            "ast_filter": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "status"},
                "right": {"type": "literal", "value": "Won"},
            },
        }

        widget_resp = await test_client.post(
            f"/instances/{instance_uuid}/widgets",
            json=widget_payload,
            headers=headers,
        )
        assert widget_resp.status_code == 201
        widget_uuid = widget_resp.json()["id"]

        # ==========================================
        # 4. ЗАПРАШИВАЕМ И ПРОВЕРЯЕМ ДАННЫЕ
        # ==========================================
        data_resp = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{widget_uuid}/data",
            headers=headers,
        )
        assert data_resp.status_code == 200

        chart_data = data_resp.json()

        # Перегоняем list of dicts в удобный словарь {label: value} для быстрой проверки
        # Ожидаем структуру от API: [{"label": "Alice", "value": 3000}, {"label": "Bob", "value": 1500}]
        result_map = {item["label"]: item["value"] for item in chart_data}

        # Проверяем, что в ответе ровно 2 менеджера (Draft и Lost не создали пустых групп)
        assert len(result_map) == 2

        # Алиса: 1000 + 2000 = 3000 (500 'Lost' проигнорировано)
        assert result_map.get("Alice") == 3000.0

        # Боб: 1500 (100 'Draft' проигнорировано)
        assert result_map.get("Bob") == 1500.0


class TestAnalyticsWidgetsPermissions:

    @pytest.mark.asyncio
    async def test_regular_user_cannot_create_widget(
        self, test_client, create_test_environment, auth_client
    ):
        """
        НЕГАТИВНЫЙ ТЕСТ: Обычный пользователь (USER) пытается создать виджет.
        Ожидаем: 403 Forbidden.
        """
        # 1. Создаем легитимное окружение (инстанс) через дефолтного создателя
        _, instance_uuid, _ = await create_test_environment()

        # 2. Переключаем test_client на сессию обычного пользователя через фикстуру auth_client
        # auth_client возвращает (client, user_object). Мы принудительно ставим ему роль USER
        client, regular_user = auth_client

        # Заголовки авторизации обычного юзера уже вшиты в client внутри фикстуры auth_client

        widget_payload = {
            "name": "Секретная выручка",
            "target_template_uuid": str(uuid.uuid4()),
            "widget_type": "BAR",
            "chart_config": {
                "axis_x": {"field": "status", "type": "categorical"},
                "axis_y": {"field": "amount", "aggregation": "SUM"},
            },
            "ast_filter": None,
        }

        # 3. Делаем запрос от лица обычного пользователя
        response = await client.post(
            f"/instances/{instance_uuid}/widgets",
            json=widget_payload,
        )

        # Система контроля доступа (RBAC Middleware или Зависимости FastAPI) должна вернуть 403
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_regular_user_cannot_modify_widget(
        self, test_client, create_test_environment, auth_client
    ):
        """
        НЕГАТИВНЫЙ ТЕСТ: Обычный пользователь (USER) пытается изменить существующий виджет.
        Ожидаем: 403 Forbidden.
        """
        # 1. Создаем инстанс и легитимные админские заголовки
        _, instance_uuid, admin_headers = await create_test_environment()

        # 2. Админ создает виджет, который мы будем пытаться сломать
        create_payload = {
            "name": "Админский график",
            "target_template_uuid": str(uuid.uuid4()),
            "widget_type": "PIE",
            "chart_config": {
                "axis_x": {"field": "category", "type": "categorical"},
                "axis_y": {"field": "id", "aggregation": "COUNT"},
            },
            "ast_filter": None,
        }
        create_resp = await test_client.post(
            f"/instances/{instance_uuid}/widgets",
            json=create_payload,
            headers=admin_headers,
        )
        assert create_resp.status_code == 201
        widget_uuid = create_resp.json()["id"]

        # 3. Переключаемся на обычного пользователя
        client, _ = auth_client

        # 4. Пользователь пытается сделать PATCH
        response = await client.patch(
            f"/instances/{instance_uuid}/widgets/{widget_uuid}",
            json={"name": "Хакнутое имя"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_regular_user_cannot_delete_widget(
        self, test_client, create_test_environment, auth_client
    ):
        """
        НЕГАТИВНЫЙ ТЕСТ: Обычный пользователь (USER) пытается удалить виджет.
        Ожидаем: 403 Forbidden.
        """
        # 1. Создаем инфраструктуру админом
        _, instance_uuid, admin_headers = await create_test_environment()

        create_payload = {
            "name": "Важный системный график",
            "target_template_uuid": str(uuid.uuid4()),
            "widget_type": "LINE",
            "chart_config": {
                "axis_x": {"field": "date", "type": "temporal"},
                "axis_y": {"field": "score", "aggregation": "AVG"},
            },
            "ast_filter": None,
        }
        create_resp = await test_client.post(
            f"/instances/{instance_uuid}/widgets",
            json=create_payload,
            headers=admin_headers,
        )
        widget_uuid = create_resp.json()["id"]

        # 2. Авторизуемся как USER
        client, _ = auth_client

        # 3. Стреляем DELETE запросом
        response = await client.delete(
            f"/instances/{instance_uuid}/widgets/{widget_uuid}"
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_regular_user_CAN_view_widget_data(
        self, test_client, create_test_environment, auth_client
    ):
        """
        ПОЗИТИВНЫЙ ТЕСТ (Проверка Read-Only прав): Обычный пользователь ДОЛЖЕН иметь доступ
        к чтению данных виджета для вывода графиков в своем интерфейсе.
        Ожидаем: 200 OK.
        """
        _, instance_uuid, admin_headers = await create_test_environment()

        # Админ создает виджет
        create_payload = {
            "name": "Публичный график",
            "target_template_uuid": str(uuid.uuid4()),
            "widget_type": "BAR",
            "chart_config": {
                "axis_x": {"field": "status", "type": "categorical"},
                "axis_y": {"field": "id", "aggregation": "COUNT"},
            },
            "ast_filter": None,
        }
        create_resp = await test_client.post(
            f"/instances/{instance_uuid}/widgets",
            json=create_payload,
            headers=admin_headers,
        )
        widget_uuid = create_resp.json()["id"]

        # Переключаемся на обычного юзера
        client, _ = auth_client

        # Делаем GET запрос на получение данных
        response = await client.get(
            f"/instances/{instance_uuid}/widgets/{widget_uuid}/data"
        )

        assert response.status_code == 403

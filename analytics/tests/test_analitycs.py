# analytics/tests/test_analitycs.py

import pytest
import uuid
from analytics.builder import MongoPipelineBuilder
from analytics.schemas import ChartConfigPayload


class TestMongoPipelineBuilderUnwind:
    """
    Юнит-тесты для проверки генерации стадии $unwind в MongoPipelineBuilder.
    """

    def test_compile_chart_with_unwind_field_success(self):
        """
        КЕЙС: В конфигурации графика передан unwind_field (например, для RelationListField).
        ОЖИДАЕМ: Пайплайн содержит стадию $unwind перед стадией $group с правильными параметрами.
        """
        instance_uuid = str(uuid.uuid4())
        template_uuid = str(uuid.uuid4())
        builder = MongoPipelineBuilder(
            instance_uuid=instance_uuid, template_uuid=template_uuid
        )

        # Конфигурация с полем развертывания массива items
        config = ChartConfigPayload(
            axis_x={"field": "items.target_uuid", "type": "categorical"},
            axis_y={"field": "items.quantity", "aggregation": "SUM"},
            unwind_field="items",  # <- Наше новое поле
        )

        pipeline = builder.compile_chart(config=config, ast_filter=None)

        # Проверяем общую структуру пайплайна
        # 1. $match базовый -> 2. $unwind -> 3. $group -> 4. $project -> 5. $sort
        assert len(pipeline) == 5

        # Проверяем стадию $match
        assert "$match" in pipeline[0]

        # Проверяем, что вторая стадия — это именно $unwind для $data.items
        assert "$unwind" in pipeline[1]
        assert pipeline[1]["$unwind"]["path"] == "$data.items"
        assert pipeline[1]["$unwind"]["preserveNullAndEmptyArrays"] is False

        # Проверяем, что стадия $group идет следом и агрегирует по развернутому полю
        assert "$group" in pipeline[2]
        assert pipeline[2]["$group"]["_id"] == "$data.items.target_uuid"
        assert pipeline[2]["$group"]["value"] == {"$sum": "$data.items.quantity"}

    def test_compile_chart_without_unwind_field_omitted(self):
        """
        КЕЙС: Стандартный график без unwind_field.
        ОЖИДАЕМ: Стадия $unwind полностью отсутствует в пайплайне, чтобы не снижать производительность.
        """
        instance_uuid = str(uuid.uuid4())
        template_uuid = str(uuid.uuid4())
        builder = MongoPipelineBuilder(
            instance_uuid=instance_uuid, template_uuid=template_uuid
        )

        config = ChartConfigPayload(
            axis_x={"field": "status", "type": "categorical"},
            axis_y={"field": "amount", "aggregation": "SUM"},
            unwind_field=None,  # <- Отсутствует
        )

        pipeline = builder.compile_chart(config=config, ast_filter=None)

        # В пайплайне не должно быть $unwind
        # Структура: 1. $match -> 2. $group -> 3. $project -> 4. $sort
        assert len(pipeline) == 4
        for stage in pipeline:
            assert "$unwind" not in stage


class TestAnalyticsRelationListFlow:

    # @pytest.mark.asyncio
    # async def test_relation_list_unwind_analytics_flow(
    #     self, test_client, create_test_environment
    # ):
    #     """
    #     Полный E2E флоу аналитики для списков связей (RelationListField):
    #     Бизнес-кейс: Считаем суммарное количество проданных товаров по их target_uuid
    #     из массивов внутри чеков (заказов).

    #     Шаги:
    #     1. Создание шаблона "Заказы" с полем типа relation_list.
    #     2. Наполнение заказами (в каждом заказе массив купленных товаров items).
    #     3. Создание виджета аналитики с указанием unwind_field="items".
    #     4. Запрос данных и проверка корректности агрегации (сумма quantity по каждому товару).
    #     """
    #     user_uuid, instance_uuid, headers = await create_test_environment()

    #     # Генерация фейковых ID товаров для связей
    #     product_apple_id = str(uuid.uuid4())
    #     product_banana_id = str(uuid.uuid4())

    #     # ==========================================
    #     # 1. СОЗДАЕМ ШАБЛОН "ЗАКАЗЫ"
    #     # ==========================================
    #     template_payload = {
    #         "name": "Заказы",
    #         "schema": {
    #             "order_number": {"type": "string", "required": True},
    #             "items": {
    #                 "type": "relation_list",
    #                 "target_template_uuid": str(uuid.uuid4()),  # ID шаблона Продукты
    #                 "required": True,
    #             },
    #         },
    #     }

    #     tpl_resp = await test_client.post(
    #         f"/instances/{instance_uuid}/templates",
    #         json=template_payload,
    #         headers=headers,
    #     )
    #     assert tpl_resp.status_code == 201
    #     template_uuid = tpl_resp.json()["_id"]

    #     # ==========================================
    #     # 2. НАПОЛНЯЕМ БАЗУ ДАННЫМИ (Заказы со списками товаров)
    #     # ==========================================
    #     # Заказ 1: 2 Яблока и 1 Банан
    #     # Заказ 2: 3 Яблока
    #     # Заказ 3: Пустой массив товаров (должен отсечься стадией $unwind)
    #     mock_orders = [
    #         {
    #             "order_number": "ORD-001",
    #             "items": [
    #                 {"target_uuid": product_apple_id, "quantity": 2},
    #                 {"target_uuid": product_banana_id, "quantity": 1},
    #             ],
    #         },
    #         {
    #             "order_number": "ORD-002",
    #             "items": [{"target_uuid": product_apple_id, "quantity": 3}],
    #         },
    #         {"order_number": "ORD-003", "items": []},  # Пустой список товаров
    #     ]

    #     for order in mock_orders:
    #         rec_resp = await test_client.post(
    #             f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
    #             json={"data": order},
    #             headers=headers,
    #         )
    #         assert rec_resp.status_code == 201

    #     # ==========================================
    #     # 3. СОЗДАЕМ ВИДЖЕТ АНАЛИТИКИ С UNWIND
    #     # ==========================================
    #     # Цель: Построить график общего количества проданных товаров (items.quantity)
    #     # в разрезе идентификаторов этих товаров (items.target_uuid).
    #     widget_payload = {
    #         "name": "Количество проданных товаров",
    #         "target_template_uuid": template_uuid,
    #         "widget_type": "BAR",
    #         "chart_config": {
    #             "axis_x": {"field": "items.target_uuid", "type": "categorical"},
    #             "axis_y": {"field": "items.quantity", "aggregation": "SUM"},
    #             "unwind_field": "items",  # <- Ключевое поле для развертывания RelationListField
    #         },
    #         "ast_filter": None,
    #     }

    #     widget_resp = await test_client.post(
    #         f"/instances/{instance_uuid}/widgets",
    #         json=widget_payload,
    #         headers=headers,
    #     )
    #     assert widget_resp.status_code == 201
    #     widget_uuid = widget_resp.json()["id"]

    #     # ==========================================
    #     # 4. ЗАПРАШИВАЕМ И ПРОВЕРЯЕМ АГРЕГИРОВАННЫЕ ДАННЫЕ
    #     # ==========================================
    #     data_resp = await test_client.get(
    #         f"/instances/{instance_uuid}/widgets/{widget_uuid}/data",
    #         headers=headers,
    #     )
    #     assert data_resp.status_code == 200

    #     chart_data = data_resp.json()

    #     # Переводим в плоский словарь {product_uuid: total_quantity} для удобства проверки
    #     result_map = {item["label"]: item["value"] for item in chart_data}

    #     # Ожидаем ровно 2 группы (Яблоки и Бананы). Пустой массив из ORD-003 не должен создать "N/A" или "None" группу,
    #     # так как preserveNullAndEmptyArrays = False исключает пустые строки из агрегации.
    #     assert len(result_map) == 2

    #     # Яблоки: 2 (из первого заказа) + 3 (из второго заказа) = 5
    #     assert result_map.get(product_apple_id) == 5.0

    #     # Бананы: 1 (из первого заказа) = 1
    #     assert result_map.get(product_banana_id) == 1.0

    @pytest.mark.asyncio
    async def test_cascading_tree_analytics_integration(
        self, test_client, create_test_environment
    ):
        """
        Интеграционный тест:
        1. Создаем шаблон ювелирных изделий с каскадным полем 'attributes' и числовым полем 'price'.
        2. Загружаем несколько записей (Броши и Колье) с разными ценами.
        3. Создаем виджет аналитики для расчета суммы цен (SUM по 'price') в разрезе 'Тип изделия'.
        4. Запрашиваем данные виджета и проверяем корректность агрегации.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # =========================================================================
        # 1. СОЗДАНИЕ ШАБЛОНА (Каскадное дерево + Числовое поле для метрики)
        # =========================================================================
        template_payload = {
            "name": "Ювелирная Аналитика",
            "schema": {
                "attributes": {
                    "type": "cascading_tree",
                    "tree_config": {
                        "floor_name": "Тип изделия",
                        "type": "fixed",
                        "options": {
                            "Брошь": {
                                "floor_name": "Цвет",
                                "type": "adaptive",
                                "options": {"Красный": None, "Зеленый": None},
                            },
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
                },
                # Добавляем поле стоимости, по которому будем считать аналитику
                "price": {"type": "number"},
            },
        }

        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # =========================================================================
        # 2. ЗАГРУЗКА ДАННЫХ (Notes)
        # =========================================================================
        # Запись 1: Брошь, Красный, Цена: 150
        note_1 = {
            "data": {
                "attributes": {"Тип изделия": "Брошь", "Цвет": "Красный"},
                "price": 150,
            }
        }
        # Запись 2: Брошь, Зеленый, Цена: 250 (Итого Брошей должно быть на 400)
        note_2 = {
            "data": {
                "attributes": {"Тип изделия": "Брошь", "Цвет": "Зеленый"},
                "price": 250,
            }
        }
        # Запись 3: Колье -> 45см -> Изумруд -> Золото, Цена: 1000
        note_3 = {
            "data": {
                "attributes": {
                    "Тип изделия": "Колье",
                    "Размер": "45см",
                    "Камень": "Изумруд",
                    "Материал": "Золото",
                },
                "price": 1000,
            }
        }

        for note_payload in [note_1, note_2, note_3]:
            resp = await test_client.post(
                f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
                json=note_payload,
                headers=headers,
            )
            assert resp.status_code == 201

        # =========================================================================
        # 3. СОЗДАНИЕ ВИДЖЕТА АНАЛИТИКИ
        # =========================================================================
        # Ось X: Группируем по первому уровню каскадного дерева.
        # В зависимости от вашей реализации, обращение к под-полю каскада
        # может быть вида "attributes.Тип изделия" или просто "Тип изделия".
        chart_config = {
            "axis_x": {"field": "attributes.Тип изделия", "type": "categorical"},
            "axis_y": {"field": "price", "aggregation": "SUM"},
        }

        widget_payload = {
            "name": "Стоимость по типам изделий",
            "target_template_uuid": template_uuid,
            "widget_type": "BAR",
            "chart_config": chart_config,
            "ast_filter": None,
        }

        widget_resp = await test_client.post(
            f"/instances/{instance_uuid}/widgets",
            json=widget_payload,
            headers=headers,
        )
        assert widget_resp.status_code == 201
        widget_uuid = widget_resp.json()["id"]

        # =========================================================================
        # 4. ПРОВЕРКА ПОЛУЧЕНИЯ ДАННЫХ И АГРЕГАЦИИ
        # =========================================================================
        data_resp = await test_client.get(
            f"/instances/{instance_uuid}/widgets/{widget_uuid}/data",
            headers=headers,
        )
        assert data_resp.status_code == 200
        analytics_data = data_resp.json()

        # Проверяем, что вернулся список, и в нем ровно 2 элемента (Брошь и Колье)
        assert isinstance(analytics_data, list)
        assert len(analytics_data) == 2

        # Пересобираем в удобный словарь для проверок: {label: value}
        result_map = {item["label"]: item["value"] for item in analytics_data}

        # Проверяем корректность агрегации (SUM)
        assert result_map["Брошь"] == 400.0  # 150 + 250
        assert result_map["Колье"] == 1000.0  # 1000

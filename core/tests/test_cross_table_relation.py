# core/tests/test_cross_table_relation.py

import pytest


class TestCrossTableRelations:

    @pytest.mark.asyncio
    async def test_order_total_price_with_cross_table_relation(
        self, test_client, create_test_environment
    ):
        """
        Проверка вычисления формулы через AST связывание (relation_field * field)
        при создании и обновлении записи.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон "Товары" и добавляем запись
        prod_tpl = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            headers=headers,
            json={
                "name": "Товары",
                "schema": {
                    "name": {"type": "string", "required": True},
                    "price": {"type": "number", "required": True},
                },
            },
        )
        prod_tpl_id = prod_tpl.json()["_id"]

        prod_rec = await test_client.post(
            f"/instances/{instance_uuid}/templates/{prod_tpl_id}/notes",
            headers=headers,
            json={"data": {"name": "Ноутбук", "price": 1500}},
        )
        product_uuid = prod_rec.json()["_id"]

        # 2. Создаем шаблон "Заказы" с формулой AST
        order_ast = {
            "type": "binary_op",
            "operator": "multiply",
            "left": {
                "type": "relation_field",
                "relation_column": "product_id",
                "target_field": "price",
            },
            "right": {"type": "field", "value": "quantity"},
        }

        order_tpl = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            headers=headers,
            json={
                "name": "Заказы",
                "schema": {
                    "product_id": {"type": "string", "required": True},
                    "quantity": {"type": "number", "required": True},
                    "total_price": {
                        "type": "formula",
                        "required": False,
                        "ast": order_ast,
                    },
                },
            },
        )
        order_tpl_id = order_tpl.json()["_id"]
        order_url = f"/instances/{instance_uuid}/templates/{order_tpl_id}/notes"

        # 3. Создаем запись заказа (1500 * 3 = 4500)
        res_create = await test_client.post(
            order_url,
            headers=headers,
            json={"data": {"product_id": product_uuid, "quantity": 3}},
        )
        assert res_create.status_code == 201
        assert res_create.json()["data"]["total_price"] == 4500.0
        order_uuid = res_create.json()["_id"]

        # 4. Обновляем запись заказа (1500 * 5 = 7500)
        res_update = await test_client.patch(
            f"{order_url}/{order_uuid}", headers=headers, json={"data": {"quantity": 5}}
        )
        assert res_update.status_code == 200
        assert res_update.json()["data"]["total_price"] == 7500.0

    @pytest.mark.asyncio
    async def test_order_formula_with_custom_lookup_field_by_qr(
        self, test_client, create_test_environment
    ):
        """
        Тестирование формулы AST с поиском связанного документа по кастомному текстовому полю (lookup_field).
        """
        user_uuid, instance_uuid, headers = await create_test_environment()
        base_tpl_url = f"/instances/{instance_uuid}/templates"

        # 1. Создаем базовый шаблон "Товары с QR"
        prod_schema = {
            "name": {"type": "string", "required": True},
            "qr_code": {"type": "string", "required": True},
            "price": {"type": "number", "required": True},
        }
        prod_tpl = await test_client.post(
            base_tpl_url,
            headers=headers,
            json={"name": "Товары с QR", "schema": prod_schema},
        )
        prod_tpl_id = prod_tpl.json()["_id"]

        # Добавляем товар со строковым QR-кодом
        target_qr = "QR-BROOCH-2026-XYZ"
        await test_client.post(
            f"{base_tpl_url}/{prod_tpl_id}/notes",
            headers=headers,
            json={
                "data": {"name": "Элитное Колье", "qr_code": target_qr, "price": 5000}
            },
        )

        # 2. Создаем шаблон "Заказы по QR" с lookup_field в AST
        order_ast = {
            "type": "binary_op",
            "operator": "multiply",
            "left": {
                "type": "relation_field",
                "relation_column": "product_qr",
                "lookup_field": "data.qr_code",
                "target_field": "price",
            },
            "right": {"type": "field", "value": "quantity"},
        }
        order_schema = {
            "product_qr": {"type": "string", "required": True},
            "quantity": {"type": "number", "required": True},
            "total_price": {"type": "formula", "required": False, "ast": order_ast},
        }
        order_tpl = await test_client.post(
            base_tpl_url,
            headers=headers,
            json={"name": "Заказы по QR", "schema": order_schema},
        )
        order_tpl_id = order_tpl.json()["_id"]

        # 3. Создаем запись заказа и проверяем расчет (5000 * 2 = 10000)
        order_payload = {"data": {"product_qr": target_qr, "quantity": 2}}
        res = await test_client.post(
            f"{base_tpl_url}/{order_tpl_id}/notes", headers=headers, json=order_payload
        )

        assert res.status_code == 201
        assert res.json()["data"]["total_price"] == 10000.0

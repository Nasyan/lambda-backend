# playground/tests/test_business_scenarios.py

"""Сложные бизнес-сценарии no-code платформы (task3, ГЗ-3 Фаза 3).

Режим требовательного клиента: цепочки каскадных автоматизаций, пороговая
программа лояльности на $old/$new, авто-дозаказ со склада, защита от циклов
на этапе сохранения, контракты системных экшенов, изоляция тенантов и
конкурентное списание остатков. Ассерты проверяют не только HTTP-ответы,
но и финальное состояние данных в MongoDB (и метаданных в PostgreSQL).
"""

import asyncio

import pytest
from sqlalchemy import select

from main import app
from mongo.db import get_mongo_db
from triggers.models import Trigger


async def _mongo_db_from_test_app():
    override = app.dependency_overrides[get_mongo_db]
    async for db in override():
        return db


def _order_payload(product_id: str, phone: str, cost: int, payment: str = "картой"):
    return {
        "data": {
            "product_list": [{"target_uuid": product_id, "qty": 1}],
            "client_phone": phone,
            "client_name": "Лояльный Клиент",
            "pickup": True,
            "cost": cost,
            "source": "сайт",
            "payment": payment,
            "real_cost": cost,
        }
    }


def _loyalty_accrual_trigger(orders_id: str, clients_id: str):
    """T1: оплаченный заказ начисляет клиенту баллы (UPSERT по телефону, $inc)."""
    return {
        "name": "loyalty: начисление баллов",
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_CREATE",
        "source_template_uuid": orders_id,
        "target_template_uuid": clients_id,
        "condition_ast": {
            "type": "binary_op",
            "operator": "eq",
            "left": {"type": "field", "value": "payment"},
            "right": {"type": "literal", "value": "картой"},
        },
        "payload_ast": {
            "type": "object",
            "fields": {
                "phone": {"type": "field", "value": "client_phone"},
                "cost": {"type": "field", "value": "cost"},
            },
        },
        "action_name": "UPSERT_RECORD",
        "action_params": {"search_fields": ["phone"]},
        "action_mapping_ast": {
            "type": "object",
            "fields": {
                "phone": {"type": "field", "value": "client_phone"},
                "name": {"type": "field", "value": "client_name"},
                "points": {
                    "type": "object",
                    "fields": {
                        "op": {"type": "literal", "value": "inc"},
                        "value": {"type": "field", "value": "cost"},
                    },
                },
            },
        },
    }


def _loyalty_threshold_trigger(clients_id: str, rewards_id: str, threshold: int = 100):
    """T2: ПЕРЕСЕЧЕНИЕ порога баллов (а не «больше порога») выдаёт награду.

    Идемпотентность через $old/$new: триггер срабатывает только в тот момент,
    когда points переваливают порог, и не дублирует награду на каждом
    следующем заказе.
    """
    return {
        "name": "loyalty: награда за порог",
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_UPDATE",
        "source_template_uuid": clients_id,
        "target_template_uuid": rewards_id,
        "condition_ast": {
            "type": "logical_op",
            "operator": "and",
            "left": {
                "type": "binary_op",
                "operator": "gt",
                "left": {"type": "field", "value": "$new.points"},
                "right": {"type": "literal", "value": threshold},
            },
            "right": {
                "type": "logical_op",
                "operator": "not",
                "left": {
                    "type": "binary_op",
                    "operator": "gt",
                    "left": {"type": "field", "value": "$old.points"},
                    "right": {"type": "literal", "value": threshold},
                },
            },
        },
        "payload_ast": {
            "type": "object",
            "fields": {
                "client_phone": {"type": "field", "value": "phone"},
            },
        },
        "action_name": "INSERT_RECORD",
        "action_mapping_ast": {
            "type": "object",
            "fields": {
                "client_phone": {"type": "field", "value": "phone"},
                "reward": {"type": "literal", "value": "GOLD_VOUCHER"},
            },
        },
    }


def _stock_decrement_trigger(orders_id: str, products_id: str):
    """Списание остатков по позициям заказа (итерационный DML)."""
    return {
        "name": "stock: списание остатков",
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_CREATE",
        "source_template_uuid": orders_id,
        "target_template_uuid": products_id,
        "condition_ast": {
            "type": "binary_op",
            "operator": "eq",
            "left": {"type": "field", "value": "payment"},
            "right": {"type": "literal", "value": "картой"},
        },
        "payload_ast": {"type": "field", "value": "product_list"},
        "action_name": "UPDATE_RECORD",
        "action_mapping_ast": {
            "type": "object",
            "fields": {
                "_id": {"type": "field", "value": "current_item.target_uuid"},
                "quantity_left": {
                    "type": "object",
                    "fields": {
                        "op": {"type": "literal", "value": "inc"},
                        "value": {
                            "type": "binary_op",
                            "operator": "multiply",
                            "left": {"type": "field", "value": "current_item.qty"},
                            "right": {"type": "literal", "value": -1},
                        },
                    },
                },
            },
        },
    }


def _low_stock_reorder_trigger(products_id: str, reorders_id: str, threshold: int = 3):
    """Падение остатка НИЖЕ порога ставит товар в очередь дозаказа (UPSERT)."""
    return {
        "name": "stock: авто-дозаказ",
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_UPDATE",
        "source_template_uuid": products_id,
        "target_template_uuid": reorders_id,
        "condition_ast": {
            "type": "binary_op",
            "operator": "lt",
            "left": {"type": "field", "value": "quantity_left"},
            "right": {"type": "literal", "value": threshold},
        },
        "payload_ast": {
            "type": "object",
            "fields": {
                "product_name": {"type": "field", "value": "name"},
            },
        },
        "action_name": "UPSERT_RECORD",
        "action_params": {"search_fields": ["product_name"]},
        "action_mapping_ast": {
            "type": "object",
            "fields": {
                "product_name": {"type": "field", "value": "name"},
                "status": {"type": "literal", "value": "REORDER_NEEDED"},
            },
        },
    }


class TestLoyaltyProgramCascade:
    """Цепочка: Заказ -> (T1) баллы клиента -> (T2) награда за пересечение порога."""

    @pytest.mark.asyncio
    async def test_threshold_reward_issued_exactly_once(
        self, test_client, loyalty_crm_env, trigger_factory, record_factory
    ):
        env = loyalty_crm_env
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        clients_id = env["clients_template_uuid"]
        products_id = env["products_template_uuid"]
        orders_id = env["orders_template_uuid"]
        rewards_id = env["rewards_template_uuid"]

        await trigger_factory(
            test_client,
            instance_uuid,
            headers,
            _loyalty_accrual_trigger(orders_id, clients_id),
        )
        await trigger_factory(
            test_client,
            instance_uuid,
            headers,
            _loyalty_threshold_trigger(clients_id, rewards_id, threshold=100),
        )

        product = await record_factory(
            test_client,
            instance_uuid,
            products_id,
            headers,
            name="Кольцо",
            quantity_left=50,
            cost=60,
        )
        phone = "+375297777001"
        orders_url = f"/instances/{instance_uuid}/templates/{orders_id}/notes"

        db = await _mongo_db_from_test_app()

        # Заказ 1: 50 баллов — порог 100 не пересечён, награды нет.
        resp = await test_client.post(
            orders_url, json=_order_payload(product["_id"], phone, 50), headers=headers
        )
        assert resp.status_code == 201, resp.text

        client_doc = await db["records"].find_one(
            {"template_uuid": clients_id, "data.phone": phone}
        )
        assert client_doc is not None, "T1 не создал клиента по UPSERT"
        assert client_doc["data"]["points"] == 50

        rewards_count = await db["records"].count_documents(
            {"template_uuid": rewards_id}
        )
        assert rewards_count == 0, "Награда выдана до пересечения порога"

        # Заказ 2: +60 = 110 — порог пересечён, выдана ровно одна награда.
        resp = await test_client.post(
            orders_url, json=_order_payload(product["_id"], phone, 60), headers=headers
        )
        assert resp.status_code == 201, resp.text

        client_doc = await db["records"].find_one(
            {"template_uuid": clients_id, "data.phone": phone}
        )
        assert client_doc["data"]["points"] == 110

        rewards = await db["records"].find({"template_uuid": rewards_id}).to_list(10)
        assert len(rewards) == 1, "Пересечение порога должно дать ровно одну награду"
        assert rewards[0]["data"]["client_phone"] == phone
        assert rewards[0]["data"]["reward"] == "GOLD_VOUCHER"

        # Заказ 3: +40 = 150 — порог уже был пересечён ($old > 100), дубликата нет.
        resp = await test_client.post(
            orders_url, json=_order_payload(product["_id"], phone, 40), headers=headers
        )
        assert resp.status_code == 201, resp.text

        rewards_count = await db["records"].count_documents(
            {"template_uuid": rewards_id}
        )
        assert rewards_count == 1, (
            "Награда задублировалась: threshold-crossing условие на $old/$new "
            "обязано отсекать повторные начисления"
        )


class TestCascadingStockReorder:
    """Двухзвенный каскад A->B->C: Заказ -> остатки товара -> заявка на дозаказ."""

    @pytest.mark.asyncio
    async def test_low_stock_creates_reorder_request(
        self, test_client, loyalty_crm_env, trigger_factory, record_factory
    ):
        env = loyalty_crm_env
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        products_id = env["products_template_uuid"]
        orders_id = env["orders_template_uuid"]
        reorders_id = env["reorders_template_uuid"]

        await trigger_factory(
            test_client,
            instance_uuid,
            headers,
            _stock_decrement_trigger(orders_id, products_id),
        )
        await trigger_factory(
            test_client,
            instance_uuid,
            headers,
            _low_stock_reorder_trigger(products_id, reorders_id, threshold=3),
        )

        product = await record_factory(
            test_client,
            instance_uuid,
            products_id,
            headers,
            name="Серьги",
            quantity_left=4,
            cost=200,
        )

        orders_url = f"/instances/{instance_uuid}/templates/{orders_id}/notes"
        db = await _mongo_db_from_test_app()

        # Заказ 1: остаток 4 -> 3. Порог (<3) не достигнут, дозаказа нет.
        order = _order_payload(product["_id"], "+375297777002", 200)
        resp = await test_client.post(orders_url, json=order, headers=headers)
        assert resp.status_code == 201, resp.text

        product_doc = await db["records"].find_one({"_id": product["_id"]})
        assert product_doc["data"]["quantity_left"] == 3
        assert await db["records"].count_documents({"template_uuid": reorders_id}) == 0

        # Заказ 2: остаток 3 -> 2 — каскад продолжается в Дозаказы.
        resp = await test_client.post(orders_url, json=order, headers=headers)
        assert resp.status_code == 201, resp.text

        product_doc = await db["records"].find_one({"_id": product["_id"]})
        assert product_doc["data"]["quantity_left"] == 2

        reorders = await db["records"].find({"template_uuid": reorders_id}).to_list(10)
        assert len(reorders) == 1, "Каскад второго звена не создал заявку на дозаказ"
        assert reorders[0]["data"]["product_name"] == "Серьги"
        assert reorders[0]["data"]["status"] == "REORDER_NEEDED"

        # Заказ 3: остаток 2 -> 1 — UPSERT по product_name не плодит дубликаты заявок.
        resp = await test_client.post(orders_url, json=order, headers=headers)
        assert resp.status_code == 201, resp.text
        assert (
            await db["records"].count_documents({"template_uuid": reorders_id}) == 1
        ), "Повторное падение остатка не должно дублировать заявку (UPSERT)"


class TestCycleProtectionAtSave:
    """Защита от бесконечных циклов: петля A->B->A режется валидатором на POST."""

    @pytest.mark.asyncio
    async def test_two_link_dml_cycle_rejected_with_422(
        self, test_client, loyalty_crm_env, trigger_factory, db_session
    ):
        env = loyalty_crm_env
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        clients_id = env["clients_template_uuid"]
        rewards_id = env["rewards_template_uuid"]

        forward = {
            "name": "cycle: clients -> rewards",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_UPDATE",
            "source_template_uuid": clients_id,
            "target_template_uuid": rewards_id,
            "payload_ast": {
                "type": "object",
                "fields": {"client_phone": {"type": "field", "value": "phone"}},
            },
            "action_name": "UPSERT_RECORD",
            "action_params": {"search_fields": ["client_phone"]},
            "action_mapping_ast": {
                "type": "object",
                "fields": {
                    "client_phone": {"type": "field", "value": "phone"},
                    "reward": {"type": "literal", "value": "X"},
                },
            },
        }
        await trigger_factory(test_client, instance_uuid, headers, forward)

        backward = {
            "name": "cycle: rewards -> clients (запрещённое замыкание)",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_UPDATE",
            "source_template_uuid": rewards_id,
            "target_template_uuid": clients_id,
            "payload_ast": {
                "type": "object",
                "fields": {"phone": {"type": "field", "value": "client_phone"}},
            },
            "action_name": "UPSERT_RECORD",
            "action_params": {"search_fields": ["phone"]},
            "action_mapping_ast": {
                "type": "object",
                "fields": {
                    "phone": {"type": "field", "value": "client_phone"},
                    "tier": {"type": "literal", "value": "LOOP"},
                },
            },
        }
        response = await trigger_factory(
            test_client, instance_uuid, headers, backward, expected_status=422
        )
        body = response.json()
        assert "cycle" in str(body).lower() or "цикл" in str(body).lower(), body

        # Контроль состояния PostgreSQL: второй триггер не сохранён.
        result = await db_session.execute(
            select(Trigger).where(Trigger.instance_uuid == instance_uuid)
        )
        names = [t.name for t in result.scalars().all()]
        assert "cycle: rewards -> clients (запрещённое замыкание)" not in names
        assert "cycle: clients -> rewards" in names


class TestSystemActionContracts:
    """Контракты системных экшенов: несовместимый payload-тип режется на POST."""

    @pytest.mark.asyncio
    async def test_bulk_notification_requires_list_payload(
        self, test_client, loyalty_crm_env, trigger_factory
    ):
        env = loyalty_crm_env
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        orders_id = env["orders_template_uuid"]
        clients_id = env["clients_template_uuid"]

        payload = {
            "name": "contract: VALUE в массовую рассылку",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "source_template_uuid": orders_id,
            "target_template_uuid": clients_id,
            # payload_ast возвращает VALUE (скаляр), а SEND_BULK_NOTIFICATION требует LIST
            "payload_ast": {"type": "field", "value": "client_phone"},
            "action_name": "SEND_BULK_NOTIFICATION",
        }
        response = await trigger_factory(
            test_client, instance_uuid, headers, payload, expected_status=422
        )
        body = response.json()
        assert "LIST" in str(body), body


class TestTenantIsolation:
    """Триггеры одного инстанса не видят и не трогают данные другого."""

    @pytest.mark.asyncio
    async def test_trigger_of_tenant_a_ignores_tenant_b_records(
        self, test_client, create_test_environment, trigger_factory, record_factory
    ):
        # --- Тенант A: таблицы + триггер на создание заказов
        _, instance_a, headers_a = await create_test_environment()
        templates_url_a = f"/instances/{instance_a}/templates"

        clients_a = (
            await test_client.post(
                templates_url_a,
                json={
                    "name": "Клиенты",
                    "schema": {
                        "phone": {"type": "string", "required": True, "unique": True}
                    },
                },
                headers=headers_a,
            )
        ).json()["_id"]
        orders_schema = {
            "client_phone": {"type": "string", "required": True},
            "pickup": {"type": "boolean", "required": True},
        }
        orders_a = (
            await test_client.post(
                templates_url_a,
                json={"name": "Заказы", "schema": orders_schema},
                headers=headers_a,
            )
        ).json()["_id"]

        await trigger_factory(
            test_client,
            instance_a,
            headers_a,
            {
                "name": "isolation: упрощённый upsert клиента",
                "trigger_type": "AUTOMATION",
                "event_type": "ON_RECORD_CREATE",
                "source_template_uuid": orders_a,
                "target_template_uuid": clients_a,
                "payload_ast": {
                    "type": "object",
                    "fields": {"phone": {"type": "field", "value": "client_phone"}},
                },
                "action_name": "UPSERT_RECORD",
                "action_params": {"search_fields": ["phone"]},
                "action_mapping_ast": {
                    "type": "object",
                    "fields": {"phone": {"type": "field", "value": "client_phone"}},
                },
            },
        )

        # --- Тенант B: своя пара таблиц с ТОЙ ЖЕ структурой, без триггеров
        _, instance_b, headers_b = await create_test_environment()
        templates_url_b = f"/instances/{instance_b}/templates"
        clients_b = (
            await test_client.post(
                templates_url_b,
                json={
                    "name": "Клиенты",
                    "schema": {
                        "phone": {"type": "string", "required": True, "unique": True}
                    },
                },
                headers=headers_b,
            )
        ).json()["_id"]
        orders_b = (
            await test_client.post(
                templates_url_b,
                json={"name": "Заказы", "schema": orders_schema},
                headers=headers_b,
            )
        ).json()["_id"]

        # Заказ в B не должен дёрнуть триггер тенанта A.
        await record_factory(
            test_client,
            instance_b,
            orders_b,
            headers_b,
            client_phone="+375290000099",
            pickup=True,
        )

        db = await _mongo_db_from_test_app()
        assert (
            await db["records"].count_documents({"template_uuid": clients_b}) == 0
        ), "В тенанте B не зарегистрирован триггер — клиентов быть не должно"
        assert (
            await db["records"].count_documents({"template_uuid": clients_a}) == 0
        ), "Триггер тенанта A не должен срабатывать на события чужого тенанта"

        # Контроль: заказ в A создаёт клиента ровно в A.
        await record_factory(
            test_client,
            instance_a,
            orders_a,
            headers_a,
            client_phone="+375290000100",
            pickup=True,
        )
        assert await db["records"].count_documents({"template_uuid": clients_a}) == 1
        assert await db["records"].count_documents({"template_uuid": clients_b}) == 0


class TestLiveEvalSuggestions:
    """LIVE_EVAL: летучая подсказка с составным фильтром (contains + ne)."""

    @pytest.mark.asyncio
    async def test_query_suggestions_with_contains_and_ne(
        self, test_client, loyalty_crm_env, trigger_factory, record_factory
    ):
        env = loyalty_crm_env
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        products_id = env["products_template_uuid"]
        orders_id = env["orders_template_uuid"]

        for name, qty in [
            ("Кольцо золото", 5),
            ("Кольцо серебро", 0),
            ("Браслет золотой", 7),
        ]:
            await record_factory(
                test_client,
                instance_uuid,
                products_id,
                headers,
                name=name,
                quantity_left=qty,
                cost=100,
            )

        trigger_resp = await trigger_factory(
            test_client,
            instance_uuid,
            headers,
            {
                "name": "live: подсказка по товарам в наличии",
                "trigger_type": "AUTOMATION",
                "event_type": "MANUAL",
                "source_template_uuid": orders_id,
                "target_template_uuid": products_id,
                "payload_ast": {
                    "type": "query",
                    "target_template_uuid": products_id,
                    "filters": [
                        {
                            "field": "name",
                            "operator": "contains",
                            "value": {"type": "input"},
                        },
                        {
                            "field": "quantity_left",
                            "operator": "ne",
                            "value": {"type": "literal", "value": 0},
                        },
                    ],
                    "return_fields": ["name", "quantity_left"],
                },
                "action_name": "RETURN_TO_CALLER",
            },
        )

        eval_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/{trigger_resp.json()['id']}/evaluate",
            json={"context_data": {}, "manual_input": "кольцо"},
            headers=headers,
        )
        assert eval_resp.status_code == 200, eval_resp.text
        names = sorted(item["data"]["name"] for item in eval_resp.json()["result"])
        # «Кольцо серебро» отфильтровано по ne(quantity_left, 0)
        assert names == ["Кольцо золото"]


class TestConcurrentStockDecrement:
    """Гонки: параллельные заказы конкурируют за один товар, $inc атомарен."""

    @pytest.mark.asyncio
    async def test_parallel_orders_do_not_lose_updates(
        self, concurrent_test_client, create_committed_environment
    ):
        client = concurrent_test_client
        _, instance_uuid, headers = await create_committed_environment()
        templates_url = f"/instances/{instance_uuid}/templates"

        products_id = (
            await client.post(
                templates_url,
                json={
                    "name": "Товары",
                    "schema": {
                        "name": {"type": "string", "required": True},
                        "quantity_left": {"type": "number", "required": True},
                        "cost": {"type": "number", "required": True},
                    },
                },
                headers=headers,
            )
        ).json()["_id"]

        orders_id = (
            await client.post(
                templates_url,
                json={
                    "name": "Заказы",
                    "schema": {
                        "product_list": {
                            "type": "relation_list",
                            "required": True,
                            "target_template_uuid": products_id,
                        },
                        "client_phone": {"type": "string", "required": False},
                        "pickup": {"type": "boolean", "required": True},
                        "cost": {"type": "number", "required": True},
                        "payment": {
                            "type": "select",
                            "options": ["картой", "наличкой"],
                            "required": True,
                        },
                    },
                },
                headers=headers,
            )
        ).json()["_id"]

        trigger_payload = _stock_decrement_trigger(orders_id, products_id)
        # Схема заказов в этом сценарии компактнее — подгоняем условие под неё
        trigger_resp = await client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=trigger_payload,
            headers=headers,
        )
        assert trigger_resp.status_code == 201, trigger_resp.text

        product_resp = await client.post(
            f"/instances/{instance_uuid}/templates/{products_id}/notes",
            json={"data": {"name": "Хит продаж", "quantity_left": 10, "cost": 50}},
            headers=headers,
        )
        assert product_resp.status_code == 201, product_resp.text
        product_id = product_resp.json()["_id"]

        orders_url = f"/instances/{instance_uuid}/templates/{orders_id}/notes"

        async def place_order(i: int):
            return await client.post(
                orders_url,
                json={
                    "data": {
                        "product_list": [{"target_uuid": product_id, "qty": 1}],
                        "client_phone": f"+37529000{i:04d}",
                        "pickup": True,
                        "cost": 50,
                        "payment": "картой",
                    }
                },
                headers=headers,
            )

        responses = await asyncio.gather(*(place_order(i) for i in range(5)))
        assert all(r.status_code == 201 for r in responses), [
            (r.status_code, r.text) for r in responses
        ]

        db = await _mongo_db_from_test_app()
        product_doc = await db["records"].find_one({"_id": product_id})
        assert product_doc["data"]["quantity_left"] == 5, (
            "Lost update: пять параллельных списаний обязаны дать 10-5=5 "
            f"(получили {product_doc['data']['quantity_left']})"
        )

        orders_count = await db["records"].count_documents({"template_uuid": orders_id})
        assert orders_count == 5

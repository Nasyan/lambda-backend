# playground/tests/test_engine_hardening.py

"""«Грязные» интеграционные тесты харднинга Trigger Engine v2 (task3, ГЗ-2 п.4).

Покрывают НЕ зелёный путь:
1. Идемпотентность UPDATE-триггеров через $old/$new state-tracking
   (двойной PATCH не списывает остатки повторно).
2. Частичный отказ батча (BulkWriteError проглатывается, успешные записи
   сохраняются, каскадные кандидаты — только реально записанные).
3. Стресс BatchDataLoader: 10 000 ID уходят в Mongo чанками, а не одним $in.
4. Юнит-семантика $old/$new в ASTEvaluator (включая CREATE без снимка).
"""

import pytest

from main import app
from mongo.db import get_mongo_db

from engine.atomic_writer import TargetAtomicWriter
from engine.batch_loader import BatchDataLoader
from engine.evaluator import ASTEvaluator, EvaluationScope
from engine.ast import parse_ast


async def _mongo_db_from_test_app():
    override = app.dependency_overrides[get_mongo_db]
    async for db in override():
        return db


def _idempotent_decrement_trigger(orders_id: str, products_id: str):
    """Триггер списания остатков, срабатывающий ТОЛЬКО на смену payment -> 'картой'.

    condition: $new.payment == 'картой' AND $old.payment != $new.payment —
    т.е. на сам факт ИЗМЕНЕНИЯ, а не на значение. Повторный PATCH другого
    поля оставляет condition ложным и DML не выполняется.
    """
    return {
        "name": "hardening idempotent decrement",
        "trigger_type": "AUTOMATION",
        "event_type": "ON_RECORD_UPDATE",
        "source_template_uuid": orders_id,
        "target_template_uuid": products_id,
        "condition_ast": {
            "type": "logical_op",
            "operator": "and",
            "left": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "$new.payment"},
                "right": {"type": "literal", "value": "картой"},
            },
            "right": {
                "type": "binary_op",
                "operator": "ne",
                "left": {"type": "field", "value": "$old.payment"},
                "right": {"type": "field", "value": "$new.payment"},
            },
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


class TestIdempotentUpdateTriggers:
    @pytest.mark.asyncio
    async def test_double_patch_does_not_double_decrement(
        self, test_client, setup_crm_environment
    ):
        """КЕЙС: заказ оплачен -> остатки списались; PATCH имени -> повторного списания НЕТ."""
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        headers = env["headers"]
        products_id = env["products_template_uuid"]
        orders_id = env["orders_template_uuid"]

        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers/",
            json=_idempotent_decrement_trigger(orders_id, products_id),
            headers=headers,
        )
        assert trigger_resp.status_code == 201, trigger_resp.text

        products_url = f"/instances/{instance_uuid}/templates/{products_id}/notes"
        product_resp = await test_client.post(
            products_url,
            json={"data": {"name": "Кольцо", "quantity_left": 10, "cost": 100}},
            headers=headers,
        )
        assert product_resp.status_code == 201, product_resp.text
        product_id = product_resp.json()["_id"]

        orders_url = f"/instances/{instance_uuid}/templates/{orders_id}/notes"
        order_resp = await test_client.post(
            orders_url,
            json={
                "data": {
                    "product_list": [{"target_uuid": product_id, "qty": 3}],
                    "client_phone": "+375290000001",
                    "client_name": "Анна",
                    "pickup": True,
                    "cost": 300,
                    "source": "сайт",
                    "payment": "наличкой",
                    "real_cost": 300,
                }
            },
            headers=headers,
        )
        assert order_resp.status_code == 201, order_resp.text
        order_id = order_resp.json()["_id"]
        order_url = f"{orders_url}/{order_id}"

        db = await _mongo_db_from_test_app()

        # 1. PATCH: оплата меняется на 'картой' -> условие истинно, списание -3.
        patch_paid = await test_client.patch(
            order_url,
            json={"data": {"payment": "картой"}},
            headers=headers,
        )
        assert patch_paid.status_code == 200, patch_paid.text

        product_doc = await db["records"].find_one({"_id": product_id})
        assert product_doc["data"]["quantity_left"] == 7

        # 2. PATCH другого поля: payment УЖЕ 'картой' ($old == $new) ->
        #    условие ложно, повторного списания быть не должно.
        patch_name = await test_client.patch(
            order_url,
            json={"data": {"client_name": "Анна Каренина"}},
            headers=headers,
        )
        assert patch_name.status_code == 200, patch_name.text

        product_doc = await db["records"].find_one({"_id": product_id})
        assert product_doc["data"]["quantity_left"] == 7, (
            "Двойной PATCH привёл к повторному списанию остатков — "
            "идемпотентность $old/$new нарушена"
        )

        # 3. Контрольный негатив: смена payment с 'картой' на 'наличными'
        #    тоже изменение, но $new.payment != 'картой' -> списания нет.
        patch_back = await test_client.patch(
            order_url,
            json={"data": {"payment": "наличкой"}},
            headers=headers,
        )
        assert patch_back.status_code == 200, patch_back.text
        product_doc = await db["records"].find_one({"_id": product_id})
        assert product_doc["data"]["quantity_left"] == 7


class TestPartialBulkFailure:
    @pytest.mark.asyncio
    async def test_partial_failure_keeps_successes_and_limits_cascade(
        self, test_client, setup_crm_environment
    ):
        """КЕЙС: батч из 3 UPDATE, один нарушает unique-индекс.

        bulk_write (ordered=False) проглатывает ошибку; 2 записи сохранены;
        fetch_touched_records (кандидаты каскада) содержит ровно 2 записи.
        """
        env = setup_crm_environment
        instance_uuid = env["instance_uuid"]
        products_id = env["products_template_uuid"]

        db = await _mongo_db_from_test_app()
        records = db["records"]

        index_name = "hardening_unique_sku"
        await records.create_index(
            [("data.sku", 1)],
            name=index_name,
            unique=True,
            partialFilterExpression={
                "template_uuid": products_id,
                "data.sku": {"$exists": True},
            },
        )
        try:
            base = {
                "instance_uuid": instance_uuid,
                "template_uuid": products_id,
                "version": 1,
            }
            await records.insert_many(
                [
                    {"_id": "hard-a", **base, "data": {"name": "A", "sku": "SKU-1"}},
                    {"_id": "hard-b", **base, "data": {"name": "B", "sku": "SKU-2"}},
                    {"_id": "hard-c", **base, "data": {"name": "C", "sku": "SKU-3"}},
                ]
            )

            writer = TargetAtomicWriter(
                mongo_db=db,
                instance_uuid=instance_uuid,
                target_template_uuid=products_id,
            )
            writer.add_update("hard-a", {"sku": "SKU-100"})
            # Нарушение уникальности: SKU-3 уже занят записью hard-c
            writer.add_update("hard-b", {"sku": "SKU-3"})
            writer.add_update("hard-c", {"sku": "SKU-300"})

            flush_result = await writer.flush()

            assert flush_result["failed_count"] == 1
            assert len(flush_result["write_errors"]) == 1
            assert flush_result["write_errors"][0]["code"] == 11000  # duplicate key
            assert flush_result["modified_count"] == 2

            # Состояние данных: успешные операции применены, упавшая — нет.
            doc_a = await records.find_one({"_id": "hard-a"})
            doc_b = await records.find_one({"_id": "hard-b"})
            doc_c = await records.find_one({"_id": "hard-c"})
            assert doc_a["data"]["sku"] == "SKU-100"
            assert (
                doc_b["data"]["sku"] == "SKU-2"
            ), "упавшая операция не должна менять данные"
            assert doc_c["data"]["sku"] == "SKU-300"

            # Каскадные кандидаты: ровно 2 реально записанные записи.
            touched = await writer.fetch_touched_records()
            touched_ids = {record["_id"] for record in touched}
            assert touched_ids == {"hard-a", "hard-c"}, (
                "Каскад должен запускаться ровно для записей, реально "
                "сохранённых в БД (2 из 3)"
            )
        finally:
            await records.drop_index(index_name)


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeCollection:
    """Шпион вместо motor-коллекции: считает запросы и размеры $in."""

    def __init__(self):
        self.find_calls = []

    def find(self, query, *args, **kwargs):
        self.find_calls.append(query)
        return _FakeCursor([])


class _FakeMongoDB(dict):
    def __init__(self, collection):
        super().__init__()
        self._collection = collection

    def __getitem__(self, name):
        return self._collection


class TestBatchDataLoaderStress:
    @pytest.mark.asyncio
    async def test_prefetch_10k_ids_goes_in_chunks(self):
        """СТРЕСС: 10 000 уникальных ID -> несколько чанк-запросов, не один гигантский $in."""
        spy = _FakeCollection()
        loader = BatchDataLoader(
            mongo_db=_FakeMongoDB(spy), instance_uuid="stress-instance"
        )

        record_ids = [f"rec-{i:05d}" for i in range(10_000)]
        await loader.prefetch("template-x", record_ids)

        assert len(spy.find_calls) == 10_000 // BatchDataLoader.CHUNK_SIZE
        for query in spy.find_calls:
            in_list = query["_id"]["$in"]
            assert len(in_list) <= BatchDataLoader.CHUNK_SIZE
        # Все ID ушли в базу ровно по одному разу
        seen = [rid for query in spy.find_calls for rid in query["_id"]["$in"]]
        assert len(seen) == 10_000
        assert len(set(seen)) == 10_000

    @pytest.mark.asyncio
    async def test_get_by_field_many_chunks_values(self):
        spy = _FakeCollection()
        loader = BatchDataLoader(
            mongo_db=_FakeMongoDB(spy), instance_uuid="stress-instance"
        )

        values = [f"sku-{i}" for i in range(1_201)]
        await loader.get_by_field_many("template-x", "data.sku", values)

        assert len(spy.find_calls) == 3  # 500 + 500 + 201
        for query in spy.find_calls:
            assert len(query["data.sku"]["$in"]) <= BatchDataLoader.CHUNK_SIZE


class TestOldNewStateSemantics:
    """Быстрые юниты $old/$new без БД."""

    def _changed_condition(self):
        return parse_ast(
            {
                "type": "binary_op",
                "operator": "ne",
                "left": {"type": "field", "value": "$old.status"},
                "right": {"type": "field", "value": "$new.status"},
            }
        )

    @pytest.mark.asyncio
    async def test_update_with_change_is_detected(self):
        evaluator = ASTEvaluator()
        scope = EvaluationScope(
            document={"data": {"status": "paid"}},
            instance_uuid="i",
            previous_document={"data": {"status": "new"}},
        )
        assert await evaluator.evaluate(self._changed_condition(), scope) is True

    @pytest.mark.asyncio
    async def test_update_without_change_is_ignored(self):
        evaluator = ASTEvaluator()
        scope = EvaluationScope(
            document={"data": {"status": "paid", "name": "Анна К."}},
            instance_uuid="i",
            previous_document={"data": {"status": "paid", "name": "Анна"}},
        )
        assert await evaluator.evaluate(self._changed_condition(), scope) is False

    @pytest.mark.asyncio
    async def test_create_event_old_is_none(self):
        """На CREATE снимка нет: $old.* == None, 'изменение' детектится корректно."""
        evaluator = ASTEvaluator()
        scope = EvaluationScope(
            document={"data": {"status": "new"}},
            instance_uuid="i",
            previous_document=None,
        )
        # None != "new" -> условие 'поле изменилось' истинно
        assert await evaluator.evaluate(self._changed_condition(), scope) is True

        # А прямое сравнение $old с конкретным значением даёт False, не ошибку
        eq_old = parse_ast(
            {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "$old.status"},
                "right": {"type": "literal", "value": "paid"},
            }
        )
        assert await evaluator.evaluate(eq_old, scope) is False

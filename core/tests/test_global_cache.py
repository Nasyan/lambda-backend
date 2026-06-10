from types import SimpleNamespace
from uuid import uuid4

import pytest

import config as cfg
from analytics.models import WidgetType
from analytics.schemas import WidgetUpdateRequest
from analytics.widget import WidgetService
from core.services.template import TemplateService
from engine.event_receptor import EventReceptor
from mongo.record import RecordRepository
from redisdb.cache import (
    CacheLayer,
    analytics_cache_key,
    build_cache_layer,
    template_cache_key,
    template_list_cache_key,
    triggers_cache_key,
)
from triggers.admin_service import TriggerAdminService
from triggers.models import EventType, PayloadReturnType, Trigger, TriggerType

pytestmark = pytest.mark.asyncio


class BrokenRedisClient:
    async def get(self, key: str):
        raise RuntimeError("redis unavailable")

    async def set(self, *args, **kwargs):
        raise RuntimeError("redis unavailable")

    async def delete(self, *keys: str):
        raise RuntimeError("redis unavailable")

    def scan_iter(self, match: str):
        raise RuntimeError("redis unavailable")


async def test_cache_layer_fail_open_on_redis_errors():
    cache = CacheLayer(BrokenRedisClient(), ttl=60, enabled=True)

    assert await cache.get_json("template:instance:object") is None
    await cache.set_json("template:instance:object", {"value": 1})
    await cache.delete("template:instance:object")
    await cache.delete_pattern("template:instance:*")


class CountingTemplateRepository:
    def __init__(self, template: dict):
        self.template = dict(template)
        self.get_template_calls = 0
        self.get_all_templates_calls = 0

    async def get_template(self, instance_uuid: str, template_uuid: str) -> dict:
        self.get_template_calls += 1
        return dict(self.template)

    async def get_all_templates(self, instance_uuid: str, params=None) -> list[dict]:
        self.get_all_templates_calls += 1
        return [dict(self.template)]

    async def update_template_metadata(
        self,
        instance_uuid: str,
        template_uuid: str,
        name: str,
        user_uuid: str,
    ) -> dict:
        self.template["name"] = name
        self.template["updated_by"] = user_uuid
        return dict(self.template)


async def test_template_cache_miss_then_hit(redis_clean, test_client):
    instance_uuid = uuid4()
    template_uuid = uuid4()
    template = {
        "_id": str(template_uuid),
        "instance_uuid": str(instance_uuid),
        "name": "Orders",
        "schema": {"amount": {"type": "number"}},
        "created_by": str(uuid4()),
    }
    repo = CountingTemplateRepository(template)
    cache = CacheLayer(redis_clean["TEMPLATE_CACHE_DB"], ttl=60, enabled=True)
    service = TemplateService(repo, cache=cache)

    first = await service.get_template(instance_uuid, template_uuid)
    second = await service.get_template(instance_uuid, template_uuid)

    assert first == second
    assert repo.get_template_calls == 1
    assert (
        await cache.get_json(template_cache_key(instance_uuid, template_uuid)) == first
    )


async def test_template_mutation_invalidates_object_and_lists(redis_clean):
    instance_uuid = uuid4()
    template_uuid = uuid4()
    template = {
        "_id": str(template_uuid),
        "instance_uuid": str(instance_uuid),
        "name": "Orders",
        "schema": {},
        "created_by": str(uuid4()),
    }
    repo = CountingTemplateRepository(template)
    cache = CacheLayer(redis_clean["TEMPLATE_CACHE_DB"], ttl=60, enabled=True)
    service = TemplateService(repo, cache=cache)
    template_key = template_cache_key(instance_uuid, template_uuid)
    list_key = template_list_cache_key(instance_uuid)

    await service.get_template(instance_uuid, template_uuid)
    await cache.set_json(list_key, [template])
    assert await cache.get_json(template_key) is not None
    assert await cache.get_json(list_key) is not None

    await service.update_template_metadata(
        instance_uuid=instance_uuid,
        template_uuid=template_uuid,
        name="Updated orders",
        user_uuid=uuid4(),
    )

    assert await cache.get_json(template_key) is None
    assert await cache.get_json(list_key) is None

    updated = await service.get_template(instance_uuid, template_uuid)
    assert updated["name"] == "Updated orders"
    assert repo.get_template_calls == 3


async def test_cache_enabled_false_skips_redis_reads_and_writes(
    redis_clean, monkeypatch
):
    monkeypatch.setattr(cfg, "CACHE_ENABLED", False)
    instance_uuid = uuid4()
    template_uuid = uuid4()
    repo = CountingTemplateRepository(
        {
            "_id": str(template_uuid),
            "instance_uuid": str(instance_uuid),
            "name": "No cache",
            "schema": {},
            "created_by": str(uuid4()),
        }
    )
    service = TemplateService(
        repo,
        cache=build_cache_layer("TEMPLATE_CACHE_DB", ttl=60),
    )

    await service.get_template(instance_uuid, template_uuid)
    await service.get_template(instance_uuid, template_uuid)

    assert repo.get_template_calls == 2
    assert await redis_clean["TEMPLATE_CACHE_DB"].dbsize() == 0


class FakeScalarResult:
    def __init__(self, values: list[Trigger]):
        self.values = values

    def all(self) -> list[Trigger]:
        return self.values


class FakeExecuteResult:
    def __init__(self, values: list[Trigger]):
        self.values = values

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self.values)


class CountingPgSession:
    def __init__(self, triggers: list[Trigger]):
        self.triggers = triggers
        self.execute_calls = 0

    async def execute(self, stmt):
        self.execute_calls += 1
        return FakeExecuteResult(self.triggers)


def _trigger(instance_uuid, template_uuid) -> Trigger:
    return Trigger(
        id=uuid4(),
        instance_uuid=instance_uuid,
        name="Create notification",
        trigger_type=TriggerType.AUTOMATION,
        event_type=EventType.ON_RECORD_CREATE,
        condition_ast=None,
        payload_ast={"type": "literal", "value": True},
        payload_return_type=PayloadReturnType.VALUE,
        action_mapping_ast=None,
        source_template_uuid=template_uuid,
        target_template_uuid=None,
        target_field="status",
        action_name="noop",
        action_params={"enabled": True},
        cron_expression=None,
    )


async def test_event_receptor_trigger_cache_hit_skips_pg(redis_clean):
    instance_uuid = uuid4()
    template_uuid = uuid4()
    trigger = _trigger(instance_uuid, template_uuid)
    pg_session = CountingPgSession([trigger])
    cache = CacheLayer(redis_clean["TRIGGERS_CACHE_DB"], ttl=60, enabled=True)
    receptor = EventReceptor(
        pg_session=pg_session,
        mongo_db=None,
        template_repo=SimpleNamespace(),
        trigger_cache=cache,
    )

    first = await receptor.get_subscribed_triggers(
        EventType.ON_RECORD_CREATE, str(instance_uuid), str(template_uuid)
    )
    second = await receptor.get_subscribed_triggers(
        EventType.ON_RECORD_CREATE, str(instance_uuid), str(template_uuid)
    )

    assert len(first) == len(second) == 1
    assert pg_session.execute_calls == 1
    assert second[0].id == trigger.id
    assert second[0].target_field == "status"


class DummyDb:
    def __init__(self):
        self.commits = 0

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return None


class FakeTriggerRepository:
    def __init__(self, trigger: Trigger | None = None):
        self.trigger = trigger
        self.deleted = None

    def add(self, trigger: Trigger) -> None:
        self.trigger = trigger

    async def get(self, instance_uuid, trigger_uuid):
        return self.trigger

    async def delete(self, trigger: Trigger) -> None:
        self.deleted = trigger


class TriggerPayload:
    def __init__(self, source_template_uuid):
        self.name = "Create notification"
        self.trigger_type = TriggerType.AUTOMATION
        self.condition_ast = None
        self.payload_ast = {"type": "literal", "value": True}
        self.action_mapping_ast = None
        self.source_template_uuid = source_template_uuid
        self.target_template_uuid = None
        self.target_field = None
        self.event_type = EventType.ON_RECORD_CREATE
        self.cron_expression = None
        self.action_name = "noop"
        self.action_params = {"enabled": True}

    def model_dump(self, exclude_unset: bool = False) -> dict:
        return {
            "name": self.name,
            "trigger_type": self.trigger_type,
            "condition_ast": self.condition_ast,
            "payload_ast": self.payload_ast,
            "action_mapping_ast": self.action_mapping_ast,
            "source_template_uuid": self.source_template_uuid,
            "target_template_uuid": self.target_template_uuid,
            "target_field": self.target_field,
            "event_type": self.event_type,
            "cron_expression": self.cron_expression,
            "action_name": self.action_name,
            "action_params": self.action_params,
        }


async def test_trigger_admin_create_and_delete_invalidate_instance_cache(
    redis_clean,
    monkeypatch,
):
    async def fake_validate(self, trigger_data, db, template_repo, trigger_uuid=None):
        return PayloadReturnType.VALUE

    monkeypatch.setattr(
        "triggers.admin_service.TriggerSchemaValidator.validate",
        fake_validate,
    )
    instance_uuid = uuid4()
    template_uuid = uuid4()
    cache = CacheLayer(redis_clean["TRIGGERS_CACHE_DB"], ttl=60, enabled=True)
    cache_key = triggers_cache_key(
        instance_uuid, template_uuid, EventType.ON_RECORD_CREATE
    )
    service = TriggerAdminService(
        db=DummyDb(),
        template_repo=SimpleNamespace(),
        trigger_meta_repo=SimpleNamespace(),
        trigger_cache=cache,
    )
    service.repo = FakeTriggerRepository()

    await cache.set_json(cache_key, [{"id": str(uuid4())}])
    created = await service.create_trigger(
        instance_uuid=instance_uuid,
        payload=TriggerPayload(template_uuid),
        user_uuid=uuid4(),
    )
    assert await cache.get_json(cache_key) is None

    await cache.set_json(cache_key, [{"id": str(created.id)}])
    service.repo = FakeTriggerRepository(created)
    await service.delete_trigger(instance_uuid, created.id, user_uuid=uuid4())
    assert await cache.get_json(cache_key) is None


class FakeWidgetRepository:
    widget = None
    get_calls = 0
    deleted = None

    def __init__(self, db):
        return None

    async def get(self, instance_uuid, widget_uuid):
        type(self).get_calls += 1
        return type(self).widget

    def add(self, widget):
        type(self).widget = widget

    async def delete(self, widget):
        type(self).deleted = widget


class CountingAnalyticsRepository:
    chart_calls = 0

    def __init__(self, mongo_db):
        return None

    async def get_schema_definition(self, template_uuid: str) -> dict:
        return {}

    async def get_chart_data(self, **kwargs) -> list[dict]:
        type(self).chart_calls += 1
        return [{"label": "open", "value": type(self).chart_calls}]


def _widget(instance_uuid, widget_uuid, template_uuid):
    return SimpleNamespace(
        id=widget_uuid,
        instance_uuid=instance_uuid,
        name="Status chart",
        target_template_uuid=template_uuid,
        widget_type=WidgetType.BAR,
        ast_filter=None,
        chart_config={
            "axis_x": {"field": "status", "type": "categorical"},
            "axis_y": {"field": "_id", "aggregation": "COUNT"},
        },
    )


async def test_analytics_cache_hit_and_widget_update_invalidation(
    redis_clean,
    monkeypatch,
):
    import analytics.widget as widget_module

    instance_uuid = uuid4()
    widget_uuid = uuid4()
    template_uuid = uuid4()
    FakeWidgetRepository.widget = _widget(instance_uuid, widget_uuid, template_uuid)
    FakeWidgetRepository.get_calls = 0
    CountingAnalyticsRepository.chart_calls = 0
    monkeypatch.setattr(
        widget_module, "AnalyticsWidgetRepository", FakeWidgetRepository
    )
    monkeypatch.setattr(
        widget_module, "AnalyticsRepository", CountingAnalyticsRepository
    )
    cache = CacheLayer(redis_clean["ANALYTICS_CACHE_DB"], ttl=60, enabled=True)
    cache_key = analytics_cache_key(instance_uuid, widget_uuid)

    first = await WidgetService.get_widget_data(
        widget_uuid=widget_uuid,
        instance_uuid=instance_uuid,
        db=DummyDb(),
        mongo_db=SimpleNamespace(),
        analytics_cache=cache,
    )
    second = await WidgetService.get_widget_data(
        widget_uuid=widget_uuid,
        instance_uuid=instance_uuid,
        db=DummyDb(),
        mongo_db=SimpleNamespace(),
        analytics_cache=cache,
    )

    assert first == second
    assert CountingAnalyticsRepository.chart_calls == 1
    assert await cache.get_json(cache_key) == first

    await WidgetService.update_widget(
        widget_uuid=widget_uuid,
        instance_uuid=instance_uuid,
        payload=WidgetUpdateRequest(name="Updated status chart"),
        db=DummyDb(),
        analytics_cache=cache,
    )
    assert await cache.get_json(cache_key) is None

    third = await WidgetService.get_widget_data(
        widget_uuid=widget_uuid,
        instance_uuid=instance_uuid,
        db=DummyDb(),
        mongo_db=SimpleNamespace(),
        analytics_cache=cache,
    )
    assert third == [{"label": "open", "value": 2}]
    assert CountingAnalyticsRepository.chart_calls == 2


async def test_analytics_cache_is_not_invalidated_by_record_insert(
    redis_clean,
    mongo_db,
):
    instance_uuid = uuid4()
    template_uuid = uuid4()
    widget_uuid = uuid4()
    cache = CacheLayer(redis_clean["ANALYTICS_CACHE_DB"], ttl=60, enabled=True)
    cache_key = analytics_cache_key(instance_uuid, widget_uuid)
    cached_value = [{"label": "before", "value": 1}]
    await cache.set_json(cache_key, cached_value)

    await RecordRepository(mongo_db).create_record(
        instance_uuid=str(instance_uuid),
        template_uuid=str(template_uuid),
        data={"status": "new"},
        user_uuid=str(uuid4()),
    )

    assert await cache.get_json(cache_key) == cached_value

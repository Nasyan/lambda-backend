import pytest

import logs.mongo as mongo_log
from mongo.record import RecordRepository
from mongo.template import TemplateRepository

TEST_INSTANCE_ID = "query_logging_company"
TEST_USER_ID = "query_logging_user"


class CapturingLogger:
    def __init__(self):
        self.info_events = []
        self.error_events = []

    def info(self, message, **kwargs):
        self.info_events.append((message, kwargs))

    def error(self, message, **kwargs):
        self.error_events.append((message, kwargs))


@pytest.mark.asyncio
async def test_mongo_repository_logs_query_count_duration_and_safe_payload(
    mongo_db, monkeypatch
):
    logger = CapturingLogger()
    monkeypatch.setattr(mongo_log, "logger", logger)

    template_repo = TemplateRepository(mongo_db)
    record_repo = RecordRepository(mongo_db)
    template = await template_repo.create_template(
        instance_uuid=TEST_INSTANCE_ID,
        name="Query Logging",
        schema={"email": {"type": "string", "required": True}},
        user_uuid=TEST_USER_ID,
    )

    await record_repo.create_record(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=template["_id"],
        data={"email": "sensitive@example.com"},
        user_uuid=TEST_USER_ID,
    )
    await record_repo.get_records(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=template["_id"],
        filters={"email": "sensitive@example.com"},
    )

    events = [event for _, event in logger.info_events]
    assert any(event["operation"] == "insert_one" for event in events)
    assert any(event["operation"] == "count_documents" for event in events)
    assert any(event["operation"] == "find" for event in events)
    assert all("duration_ms" in event for event in events)
    assert all("documents_affected" in event for event in events)

    record_insert_event = next(
        event
        for event in events
        if event["collection"] == "records" and event["operation"] == "insert_one"
    )
    assert record_insert_event["query"]["data_key_count"] == 1
    data_keys = record_insert_event["query"]["data_keys"]
    assert data_keys["type"] == "list"
    assert data_keys["count"] == 1
    assert "email" in data_keys["items"]
    assert "sensitive@example.com" not in str(record_insert_event["query"])

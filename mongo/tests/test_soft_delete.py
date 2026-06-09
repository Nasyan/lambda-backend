from uuid import uuid4

import pytest

from mongo.history import HistoryRepository
from mongo.record import RecordRepository
from mongo.template import TemplateRepository
from mongo.exceptions.record import RecordNotFoundError
from mongo.exceptions.template import TemplateNotFoundError

TEST_INSTANCE_ID = "soft_delete_company"
TEST_USER_ID = "soft_delete_user"


@pytest.mark.asyncio
async def test_record_soft_delete_restore_and_history_visibility(mongo_db):
    template_repo = TemplateRepository(mongo_db)
    record_repo = RecordRepository(mongo_db)
    history_repo = HistoryRepository(mongo_db)

    template = await template_repo.create_template(
        instance_uuid=TEST_INSTANCE_ID,
        name="Soft Delete Records",
        schema={"title": {"type": "string", "required": True}},
        user_uuid=TEST_USER_ID,
    )
    record = await record_repo.create_record(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=template["_id"],
        data={"title": "visible"},
        user_uuid=TEST_USER_ID,
    )
    await history_repo.log_change(
        instance_uuid=TEST_INSTANCE_ID,
        record_uuid=record["_id"],
        user_uuid=TEST_USER_ID,
        version=1,
        snapshot=record["data"],
    )

    await record_repo.delete_record(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=template["_id"],
        record_uuid=record["_id"],
    )

    raw_record = await mongo_db["records"].find_one({"_id": record["_id"]})
    assert raw_record["is_deleted"] is True

    with pytest.raises(RecordNotFoundError):
        await record_repo.get_record_by_uuid(TEST_INSTANCE_ID, record["_id"])

    active_records, active_total = await record_repo.get_records(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=template["_id"],
    )
    assert active_records == []
    assert active_total == 0
    assert await history_repo.get_record_history(TEST_INSTANCE_ID, record["_id"]) == []

    deleted_records, deleted_total = await record_repo.get_deleted_records(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=template["_id"],
    )
    assert deleted_total == 1
    assert deleted_records[0]["_id"] == record["_id"]

    restored = await record_repo.restore_record(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=template["_id"],
        record_uuid=record["_id"],
    )
    assert restored["is_deleted"] is False
    assert await history_repo.get_record_history(TEST_INSTANCE_ID, record["_id"])


@pytest.mark.asyncio
async def test_template_soft_delete_restore_cascades_records_and_history(mongo_db):
    template_repo = TemplateRepository(mongo_db)
    record_repo = RecordRepository(mongo_db)
    history_repo = HistoryRepository(mongo_db)

    template = await template_repo.create_template(
        instance_uuid=TEST_INSTANCE_ID,
        name="Soft Delete Templates",
        schema={"title": {"type": "string", "required": True}},
        user_uuid=TEST_USER_ID,
    )
    record = await record_repo.create_record(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=template["_id"],
        data={"title": "restored with template"},
        user_uuid=TEST_USER_ID,
    )
    await history_repo.log_change(
        instance_uuid=TEST_INSTANCE_ID,
        record_uuid=record["_id"],
        user_uuid=TEST_USER_ID,
        version=1,
        snapshot=record["data"],
    )

    await template_repo.delete_template(TEST_INSTANCE_ID, template["_id"])

    raw_template = await mongo_db["templates"].find_one({"_id": template["_id"]})
    raw_record = await mongo_db["records"].find_one({"_id": record["_id"]})
    assert raw_template["is_deleted"] is True
    assert raw_record["is_deleted"] is True

    with pytest.raises(TemplateNotFoundError):
        await template_repo.get_template_by_uuid(TEST_INSTANCE_ID, template["_id"])
    with pytest.raises(RecordNotFoundError):
        await record_repo.get_record_by_uuid(TEST_INSTANCE_ID, record["_id"])

    deleted_templates = await template_repo.get_deleted_templates(TEST_INSTANCE_ID)
    assert [item["_id"] for item in deleted_templates] == [template["_id"]]

    restored_template = await template_repo.restore_template(
        TEST_INSTANCE_ID, template["_id"]
    )
    assert restored_template["is_deleted"] is False
    assert await record_repo.get_record_by_uuid(TEST_INSTANCE_ID, record["_id"])
    assert await history_repo.get_record_history(TEST_INSTANCE_ID, record["_id"])


@pytest.mark.asyncio
async def test_active_queries_ignore_legacy_documents_without_is_deleted(mongo_db):
    template_id = str(uuid4())
    record_id = str(uuid4())
    await mongo_db["templates"].insert_one(
        {
            "_id": template_id,
            "instance_uuid": TEST_INSTANCE_ID,
            "name": "Legacy Template",
            "schema": {},
            "version": 1,
            "created_by": TEST_USER_ID,
        }
    )
    await mongo_db["records"].insert_one(
        {
            "_id": record_id,
            "instance_uuid": TEST_INSTANCE_ID,
            "template_uuid": template_id,
            "data": {"name": "legacy"},
            "version": 1,
            "created_by": TEST_USER_ID,
        }
    )

    template_repo = TemplateRepository(mongo_db)
    record_repo = RecordRepository(mongo_db)

    assert await template_repo.get_template_by_uuid(TEST_INSTANCE_ID, template_id)
    records, total = await record_repo.get_records(TEST_INSTANCE_ID, template_id)
    assert total == 1
    assert records[0]["_id"] == record_id

import pytest

from main import app
from mongo.db import get_mongo_db
from mongo.history import HistoryRepository


@pytest.mark.asyncio
async def test_template_soft_delete_restore_api_cascades_records_and_history(
    test_client, create_test_environment
):
    user_uuid, instance_uuid, headers = await create_test_environment()
    template_payload = {
        "name": "API Soft Delete Templates",
        "schema": {"title": {"type": "string", "required": True}},
    }

    create_template_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json=template_payload,
        headers=headers,
    )
    assert create_template_resp.status_code == 201
    template_uuid = create_template_resp.json()["_id"]

    create_record_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json={"data": {"title": "record restored with template"}},
        headers=headers,
    )
    assert create_record_resp.status_code == 201
    record_uuid = create_record_resp.json()["_id"]

    mongo_db = await anext(app.dependency_overrides[get_mongo_db]())
    history_repo = HistoryRepository(mongo_db)
    await history_repo.log_change(
        instance_uuid=instance_uuid,
        record_uuid=record_uuid,
        user_uuid=user_uuid,
        version=1,
        snapshot={"title": "record restored with template"},
    )

    delete_resp = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{template_uuid}",
        headers=headers,
    )
    assert delete_resp.status_code == 204

    get_deleted_templates_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates/deleted",
        headers=headers,
    )
    assert get_deleted_templates_resp.status_code == 200
    deleted_templates = get_deleted_templates_resp.json()
    assert [item["_id"] for item in deleted_templates] == [template_uuid]
    assert deleted_templates[0]["is_deleted"] is True

    get_template_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates/{template_uuid}",
        headers=headers,
    )
    assert get_template_resp.status_code == 404

    active_records_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        headers=headers,
    )
    assert active_records_resp.status_code == 200
    assert active_records_resp.json()["total"] == 0

    deleted_records_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes/deleted",
        headers=headers,
    )
    assert deleted_records_resp.status_code == 200
    assert deleted_records_resp.json()["total"] == 1

    history_hidden_resp = await test_client.get(
        f"/history/record/{record_uuid}/",
        headers=headers,
    )
    assert history_hidden_resp.status_code == 200
    assert history_hidden_resp.json()["history"] == []

    restore_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/restore",
        headers=headers,
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["is_deleted"] is False

    restored_records_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        headers=headers,
    )
    assert restored_records_resp.status_code == 200
    assert restored_records_resp.json()["total"] == 1

    history_restored_resp = await test_client.get(
        f"/history/record/{record_uuid}/",
        headers=headers,
    )
    assert history_restored_resp.status_code == 200
    assert len(history_restored_resp.json()["history"]) == 1


@pytest.mark.asyncio
async def test_record_soft_delete_restore_api(test_client, create_test_environment):
    _, instance_uuid, headers = await create_test_environment()
    template_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={
            "name": "API Soft Delete Records",
            "schema": {"title": {"type": "string", "required": True}},
        },
        headers=headers,
    )
    assert template_resp.status_code == 201
    template_uuid = template_resp.json()["_id"]

    record_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json={"data": {"title": "soft deleted record"}},
        headers=headers,
    )
    assert record_resp.status_code == 201
    record_uuid = record_resp.json()["_id"]

    delete_resp = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes/{record_uuid}",
        headers=headers,
    )
    assert delete_resp.status_code == 204

    active_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        headers=headers,
    )
    assert active_resp.status_code == 200
    assert active_resp.json()["total"] == 0

    deleted_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes/deleted",
        headers=headers,
    )
    assert deleted_resp.status_code == 200
    assert deleted_resp.json()["total"] == 1
    assert deleted_resp.json()["results"][0]["is_deleted"] is True

    patch_resp = await test_client.patch(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes/{record_uuid}",
        json={"data": {"title": "should remain deleted"}},
        headers=headers,
    )
    assert patch_resp.status_code == 404

    restore_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes/{record_uuid}/restore",
        headers=headers,
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["is_deleted"] is False

    restored_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        headers=headers,
    )
    assert restored_resp.status_code == 200
    assert restored_resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_restore_deleted_template_conflicting_name_returns_409(
    test_client, create_test_environment
):
    _, instance_uuid, headers = await create_test_environment()
    payload = {
        "name": "Restore Conflict",
        "schema": {"title": {"type": "string", "required": True}},
    }

    first_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json=payload,
        headers=headers,
    )
    assert first_resp.status_code == 201
    deleted_template_uuid = first_resp.json()["_id"]
    delete_resp = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{deleted_template_uuid}",
        headers=headers,
    )
    assert delete_resp.status_code == 204

    second_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json=payload,
        headers=headers,
    )
    assert second_resp.status_code == 201

    restore_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates/{deleted_template_uuid}/restore",
        headers=headers,
    )
    assert restore_resp.status_code == 409

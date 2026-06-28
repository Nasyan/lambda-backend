import pytest


@pytest.mark.asyncio
async def test_force_delete_template_success(
    test_client,
    create_test_environment,
    mongo_db,  # <-- Добавили фикстуру БД для прямой проверки
):
    """
    Позитивный сценарий: Полный каскадный цикл жесткого удаления.
    Создаем шаблон -> Создаем запись (record) -> Удаляем мягко -> Удаляем жестко.
    Проверяем, что эндпоинты возвращают правильные статусы, а шаблон и записи исчезли.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон таблицы
    base_payload = {
        "name": "Таблица под снос",
        "schema": {"title": {"type": "string", "required": True}},
    }
    create_template_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    assert create_template_resp.status_code == 201
    template_uuid = create_template_resp.json()["_id"]

    # 2. Создаем запись (record) в этой таблице
    record_payload = {"data": {"title": "Секретные данные"}}
    create_record_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json=record_payload,
        headers=headers,
    )
    assert create_record_resp.status_code == 201

    # Убеждаемся перед удалением, что запись физически появилась в MongoDB
    initial_records_count = await mongo_db["records"].count_documents(
        {"template_uuid": template_uuid}
    )
    assert initial_records_count == 1

    # 3. Выполняем ПЕРВОЕ (мягкое) удаление шаблона (is_deleted=True)
    delete_resp = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{template_uuid}", headers=headers
    )
    assert delete_resp.status_code == 204

    # Проверяем, что в списке активных его больше нет
    get_active_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates", headers=headers
    )
    assert not any(t["_id"] == template_uuid for t in get_active_resp.json())

    # Проверяем, что он появился в корзине
    get_deleted_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates/deleted", headers=headers
    )
    assert any(t["_id"] == template_uuid for t in get_deleted_resp.json())

    # 4. Выполняем ВТОРОЕ (жесткое) безвозвратное удаление через новый эндпоинт
    force_delete_resp = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{template_uuid}/force", headers=headers
    )
    assert force_delete_resp.status_code == 204

    # 5. Проверяем, что шаблон полностью исчез даже из корзины (hard deleted)
    get_deleted_after_force = await test_client.get(
        f"/instances/{instance_uuid}/templates/deleted", headers=headers
    )
    assert not any(t["_id"] == template_uuid for t in get_deleted_after_force.json())

    # 6. ГАРАНТИЯ КАСКАДА: Проверяем напрямую коллекции MongoDB, что там абсолютный 0 записей
    remaining_records = await mongo_db["records"].count_documents(
        {"template_uuid": template_uuid}
    )
    remaining_history = await mongo_db["records_history"].count_documents(
        {"template_uuid": template_uuid}
    )

    assert remaining_records == 0, "Записи шаблона не были каскадно удалены!"
    assert remaining_history == 0, "История записей шаблона не была каскадно удалена!"


@pytest.mark.asyncio
async def test_force_delete_active_template_fail(test_client, create_test_environment):
    """
    Негативный сценарий: Попытка жестко удалить АКТИВНЫЙ шаблон (без предварительного мягкого удаления).
    Ожидаем ошибку 404 от репозитория, так как фильтр `is_deleted: True` не найдет документ.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон
    base_payload = {
        "name": "Живой шаблон",
        "schema": {"status": {"type": "string", "required": False}},
    }
    create_template_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    assert create_template_resp.status_code == 201
    template_uuid = create_template_resp.json()["_id"]

    # 2. Пытаемся сразу шарахнуть его через /force
    force_delete_resp = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{template_uuid}/force", headers=headers
    )

    # Репозиторий выкинет TemplateNotFoundError, что превратится в 404 Not Found на клиенте
    assert force_delete_resp.status_code == 404


@pytest.mark.asyncio
async def test_force_delete_already_purged_template_fail(
    test_client, create_test_environment
):
    """
    Негативный сценарий: Попытка повторного жесткого удаления уже стертого шаблона.
    Убеждаемся, что система корректно реагирует на отсутствующий ID.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем и сразу удаляем мягко
    base_payload = {
        "name": "Шаблон-призрак",
        "schema": {"meta": {"type": "string", "required": False}},
    }
    create_template_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_template_resp.json()["_id"]

    await test_client.delete(
        f"/instances/{instance_uuid}/templates/{template_uuid}", headers=headers
    )

    # 2. Первый force delete — должен пройти успешно
    first_force = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{template_uuid}/force", headers=headers
    )
    assert first_force.status_code == 204

    # 3. Второй force delete того же UUID — должен выплюнуть 404
    second_force = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{template_uuid}/force", headers=headers
    )
    assert second_force.status_code == 404

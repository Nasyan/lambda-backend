# minio/tests/test_api.py

import pytest
import httpx
from httpx import AsyncClient
import uuid
from users.models import UserRole, UserPermissions


class TestStorageAPI:
    @pytest.mark.asyncio
    async def test_full_s3_lifecycle_success(self, minio_client: AsyncClient):
        """
        Интеграционный тест полного жизненного цикла файла в S3:
        Использует изолированный minio_client.
        """
        instance_uuid = str(uuid.uuid4())
        template_uuid = str(uuid.uuid4())
        filename = "test_avatar.png"
        file_content = b"fake-binary-image-data-stream"

        # --- ШАГ 1: Запрашиваем ссылку на загрузку (Upload Intent) ---
        upload_payload = {
            "filename": filename,
            "instance_uuid": instance_uuid,
            "template_uuid": template_uuid,
        }

        intent_response = await minio_client.post(
            "/storage/upload-intent", json=upload_payload
        )
        assert intent_response.status_code == 200

        intent_data = intent_response.json()
        assert "upload_url" in intent_data
        assert "file_path" in intent_data

        upload_url = intent_data["upload_url"]
        file_path = intent_data["file_path"]

        assert (
            f"instances/{instance_uuid}/templates/{template_uuid}/uploads" in file_path
        )

        # --- ШАГ 2: Имитируем фронтенд (Загружаем файл напрямую в MinIO) ---
        async with httpx.AsyncClient() as direct_client:
            minio_put_response = await direct_client.put(
                upload_url,
                content=file_content,
                headers={"Content-Type": "application/octet-stream"},
            )
            assert minio_put_response.status_code == 200

        # --- ШАГ 3: Запрашиваем ссылку на скачивание/рендеринг файла ---
        # ИСПРАВЛЕНО: убран слэш перед знаком вопроса
        download_response = await minio_client.get(
            f"/storage/download?file_path={file_path}"
        )
        assert download_response.status_code == 200

        download_data = download_response.json()
        assert "download_url" in download_data
        assert (
            "sig=" in download_data["download_url"]
            or "Signature=" in download_data["download_url"]
        )

        # --- ШАГ 4: Удаляем файл из хранилища ---
        delete_payload = {"file_path": file_path}
        delete_response = await minio_client.request(
            method="DELETE", url="/storage/delete", json=delete_payload
        )
        assert delete_response.status_code == 204

        # --- ШАГ 5: Проверяем, что файла больше нет (Должен вернуть 404) ---
        # ИСПРАВЛЕНО: убран слэш перед знаком вопроса
        gone_response = await minio_client.get(
            f"/storage/download?file_path={file_path}"
        )
        assert gone_response.status_code == 404
        assert gone_response.json()["error_code"] == "STORAGE_FILE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_get_download_link_not_found(self, minio_client: AsyncClient):
        """Проверяем поведение системы при попытке запросить несуществующий файл."""
        fake_path = "instances/fake-inst/templates/fake-tpl/uploads/does-not-exist.png"

        # ИСПРАВЛЕНО: убран слэш перед знаком вопроса
        response = await minio_client.get(f"/storage/download?file_path={fake_path}")
        assert response.status_code == 404
        assert response.json()["error_code"] == "STORAGE_FILE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_delete_file_not_found(self, minio_client: AsyncClient):
        """Проверяем, что роутер удаления падает с 404, если файла в S3 нет."""
        fake_path = "instances/fake-inst/templates/fake-tpl/uploads/missing.png"

        response = await minio_client.request(
            method="DELETE", url="/storage/delete", json={"file_path": fake_path}
        )
        assert response.status_code == 404
        assert response.json()["error_code"] == "STORAGE_FILE_NOT_FOUND"


class TestStorageIntegration:

    @pytest.mark.asyncio
    async def test_create_template_with_image_and_insert_record(
        self, minio_client, db_session, create_test_environment
    ):
        """
        Сквозной интеграционный тест (End-to-End):
        Проверяет создание схемы с типом 'image' и физическую валидацию файла.
        """
        # --- ШАГ 1: Создаем чистое окружение (CREATOR + Инстанс + JWT) ---
        user_uuid, instance_uuid, auth_headers = await create_test_environment(
            role=UserRole.CREATOR
        )

        # Докидываем права на инструменты (так как в фабрике этого нет, пишем напрямую через db_session)
        permissions = UserPermissions(
            user_uuid=uuid.UUID(user_uuid), allowed_tools=["all"]
        )
        db_session.add(permissions)
        await db_session.commit()

        filename = "avatar.png"
        file_content = b"fake-png-binary-data-from-disk-stream"

        # --- ШАГ 2: Создаем шаблон со столбцом-картинкой ---
        template_payload = {
            "name": "Профили Авторов",
            "schema": {
                "avatar": {"type": "image", "required": True},
                "bio": {"type": "string", "required": False},
            },
        }

        template_resp = await minio_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=auth_headers,
        )
        assert template_resp.status_code == 201
        template_uuid = template_resp.json()["_id"]

        # --- ШАГ 3: Запрашиваем у MinIO ссылку на загрузку файла ---
        intent_payload = {
            "filename": filename,
            "instance_uuid": instance_uuid,
            "template_uuid": template_uuid,
        }
        intent_resp = await minio_client.post(
            "/storage/upload-intent", json=intent_payload, headers=auth_headers
        )
        assert intent_resp.status_code == 200

        intent_data = intent_resp.json()
        upload_url = intent_data["upload_url"]
        file_path = intent_data["file_path"]

        # --- ШАГ 4: Имитируем фронтенд (PUT на порт MinIO 9002) ---
        async with httpx.AsyncClient() as direct_client:
            minio_resp = await direct_client.put(
                upload_url,
                content=file_content,
                headers={"Content-Type": "application/octet-stream"},
            )
            assert minio_resp.status_code == 200

        # --- ШАГ 5: Создаем запись (record) в нашей таблице ---
        record_payload = {
            "data": {
                "avatar": file_path,
                "bio": "Разработчик CRM системы. Vibe coding.",
            }
        }

        record_resp = await minio_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json=record_payload,
            headers=auth_headers,
        )

        assert record_resp.status_code == 201
        record_data = record_resp.json()

        assert record_data["data"]["avatar"] == file_path
        assert record_data["data"]["bio"] == "Разработчик CRM системы. Vibe coding."

    @pytest.mark.asyncio
    async def test_create_record_with_fake_image_path_fails(
        self, minio_client, db_session, create_test_environment
    ):
        """
        Негативный тест: Проверяем, что если передать путь к файлу,
        которого физически нет в MinIO, асинхронный валидатор вернет 400.
        """
        # 1. Создаем окружение
        user_uuid, instance_uuid, auth_headers = await create_test_environment(
            role=UserRole.CREATOR
        )

        permissions = UserPermissions(
            user_uuid=uuid.UUID(user_uuid), allowed_tools=["all"]
        )
        db_session.add(permissions)
        await db_session.commit()

        # 2. Создаем шаблон
        template_payload = {
            "name": "Таблица с проверкой фейков",
            "schema": {"photo": {"type": "image", "required": True}},
        }
        template_resp = await minio_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=auth_headers,
        )
        assert template_resp.status_code == 201
        template_uuid = template_resp.json()["_id"]

        # 3. Пытаемся отправить выдуманный путь в S3
        bad_record_payload = {
            "data": {
                "photo": f"instances/{instance_uuid}/templates/{template_uuid}/uploads/hacker_file.png"
            }
        }

        response = await minio_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json=bad_record_payload,
            headers=auth_headers,
        )

        assert response.status_code == 422

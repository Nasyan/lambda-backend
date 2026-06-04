# minio/service.py

import os
import uuid
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError
from config import MINIO_DEFAULT_BUCKET

# Импортируем профессиональные доменные ошибки хранилища
from minio.exceptions.service import (
    StorageInfrastructureError,
    StorageFileNotFoundError,
    StorageURLGenerationError,
)


class S3StorageService:
    def __init__(self, s3_client):
        """
        Инициализирует сервис.
        s3_client передается снаружи (например, из Depends(get_s3_client)).
        """
        self.client = s3_client
        self.bucket = MINIO_DEFAULT_BUCKET

    def _generate_file_path(
        self,
        instance_uuid: str,
        template_uuid: str,
        filename: str,
        record_uuid: Optional[str] = None,
    ) -> str:
        """
        Внутренний метод для генерации структурированного и безопасного пути к файлу в бакете.
        Предотвращает коллизии имен за счет использования UUID.
        """
        ext = os.path.splitext(filename)[1].lower()
        unique_filename = f"{uuid.uuid4()}{ext}"

        if record_uuid:
            return f"instances/{instance_uuid}/templates/{template_uuid}/records/{record_uuid}/{unique_filename}"
        return f"instances/{instance_uuid}/templates/{template_uuid}/uploads/{unique_filename}"

    async def generate_upload_url(
        self,
        instance_uuid: str,
        template_uuid: str,
        filename: str,
        expires_in: int = 3600,
    ) -> Dict[str, Any]:
        """
        Шаг 1. Генерирует Pre-signed URL для загрузки файла (PUT запрос) напрямую из браузера в MinIO.
        Returns:
            dict: Содержит 'upload_url' для фронтенда и 'file_path' для последующего сохранения в MongoDB.
        """
        file_path = self._generate_file_path(instance_uuid, template_uuid, filename)

        try:
            upload_url = await self.client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": file_path,
                    "ContentType": "application/octet-stream",
                },
                ExpiresIn=expires_in,
            )
            return {
                "upload_url": upload_url,
                "file_path": file_path,
            }
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code in (
                "NoSuchBucket",
                "InvalidAccessKeyId",
                "SignatureDoesNotMatch",
            ):
                raise StorageInfrastructureError(
                    message="Критический сбой конфигурации облачного хранилища.",
                    raw_error=str(e),
                )
            raise StorageURLGenerationError(
                message=f"Не удалось сгенерировать ссылку загрузки для файла: {filename}"
            )

    async def generate_download_url(
        self, file_path: str, expires_in: int = 3600
    ) -> str:
        """
        Шаг 4. Генерирует временную безопасную ссылку на скачивание/просмотр приватного файла.
        Используется при отдаче данных таблицы пользователю.
        """
        try:
            # Сначала проверяем физическое наличие перед генерацией ссылки,
            # чтобы фронтенд сразу получал 404, а не битую ссылку
            await self.client.head_object(Bucket=self.bucket, Key=file_path)

            return await self.client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket, "Key": file_path},
                ExpiresIn=expires_in,
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404" or error_code == "NoSuchKey":
                raise StorageFileNotFoundError(file_path=file_path)
            raise StorageInfrastructureError(raw_error=str(e))

    async def delete_file(self, file_path: str) -> None:
        """
        Удаляет объект из хранилища MinIO.
        Пригодится, когда пользователь очищает ячейку с картинкой или удаляет всю строку.
        """
        try:
            await self.client.delete_object(Bucket=self.bucket, Key=file_path)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404" or error_code == "NoSuchKey":
                # Если файла и так нет — операция удаления считается успешной (идемпотентность)
                return
            raise StorageInfrastructureError(raw_error=str(e))

    async def file_exists(self, file_path: str) -> bool:
        """
        Проверяет, физически ли существует файл в бакете.
        Идеально подходит для валидатора ImageField, чтобы проверить,
        не пытается ли пользователь сохранить ссылку на несуществующий файл.
        """
        try:
            await self.client.head_object(Bucket=self.bucket, Key=file_path)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404" or error_code == "NoSuchKey":
                return False
            # Если упала сеть или нет прав доступа, это не значит, что файла нет.
            # Это инфраструктурный сбой — пишем честно.
            raise StorageInfrastructureError(
                message="Не удалось проверить существование файла из-за сетевого сбоя хранилища.",
                raw_error=str(e),
            )

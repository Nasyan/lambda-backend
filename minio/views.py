# minio/views.py

from fastapi import APIRouter, Depends, status
from minio.service import S3StorageService
from minio.dependencies import get_s3_service
from minio.exceptions.service import StorageFileNotFoundError
from minio.schemas import (
    UploadIntentRequest,
    UploadIntentResponse,
    DownloadResponse,
    FileDeleteRequest,
)

router = APIRouter(prefix="/storage", tags=["storage"])


@router.post(
    "/upload-intent/",
    response_model=UploadIntentResponse,
    status_code=status.HTTP_200_OK,
    summary="Получить ссылку для загрузки файла",
)
async def get_upload_link(
    payload: UploadIntentRequest, s3_service: S3StorageService = Depends(get_s3_service)
):
    """
    **Шаг 1 двухэтапной загрузки.**

    Фронтенд передает имя файла и контекст таблицы. Бэкенд возвращает pre-signed URL
    для прямой загрузки в MinIO и сгенерированный `file_path`.

    После этого фронтенд делает `PUT` запрос с файлом на `upload_url`, а затем сохраняет `file_path` в ячейку record.
    """
    result = await s3_service.generate_upload_url(
        instance_uuid=payload.instance_uuid,
        template_uuid=payload.template_uuid,
        filename=payload.filename,
    )
    return result


@router.get(
    "/download/",
    response_model=DownloadResponse,
    summary="Получить временную ссылку на чтение/отображение файла",
)
async def get_download_link(
    file_path: str, s3_service: S3StorageService = Depends(get_s3_service)
):
    """
    **Шаг 4 (Отображение данных).**

    Когда фронтенду нужно отрендерить картинку из ячейки таблицы, он передает сохраненный
    в MongoDB `file_path` сюда и получает временный подписанный URL для тега `<img>`.
    """
    # Сначала проверяем, а существует ли файл вообще, чтобы не генерировать ссылку на 404
    if not await s3_service.file_exists(file_path):
        raise StorageFileNotFoundError(file_path=file_path)

    url = await s3_service.generate_download_url(file_path=file_path, expires_in=3600)
    return DownloadResponse(download_url=url)


@router.delete(
    "/delete/",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить файл из S3 хранилища",
)
async def delete_file(
    payload: FileDeleteRequest, s3_service: S3StorageService = Depends(get_s3_service)
):
    """
    Очистка дискового пространства.

    Вызывается, когда пользователь стирает значение из ячейки "Картинка"
    или полностью удаляет строку (record) из CRM.
    """
    if not await s3_service.file_exists(payload.file_path):
        raise StorageFileNotFoundError(file_path=payload.file_path)

    await s3_service.delete_file(file_path=payload.file_path)
    # Возвращаем 204 No Content, так как тело ответа при успешном удалении не требуется
    return

# minio/schemas.py

from pydantic import BaseModel, Field


class UploadIntentRequest(BaseModel):
    filename: str = Field(
        ..., description="Оригинальное имя файла с расширением (например, photo.jpg)"
    )
    instance_uuid: str
    template_uuid: str


class UploadIntentResponse(BaseModel):
    upload_url: str = Field(
        ...,
        description="Временная URL-ссылка, на которую фронтенд отправляет PUT-запрос с бинарником файла",
    )
    file_path: str = Field(
        ...,
        description="Путь к файлу в S3, который фронтенд ДОЛЖЕН отправить в JSON при создании/обновлении record",
    )


class DownloadResponse(BaseModel):
    download_url: str = Field(
        ..., description="Временная ссылка для отображения картинки на фронтенде"
    )


class FileDeleteRequest(BaseModel):
    file_path: str = Field(..., description="Путь к файлу из тела record в MongoDB")

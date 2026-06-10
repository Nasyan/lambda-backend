# core/views/record.py

from uuid import UUID
from core.schemas.record import (
    CSVImportRequest,
    CSVImportResponse,
    RecordCreateRequest,
    RecordUpdateRequest,
    RecordResponse,
)
from core.permissions import RequireTool
from users.models import AppTools
from core.services.record import RecordService
from core.dependencies import get_record_service
from csvloader import CSVImportValidationError
import json
from fastapi import APIRouter, Depends, Query, HTTPException, Response
from typing import Optional
from minio.db import get_s3_client
from minio.service import S3StorageService
from core.schemas.record import PaginatedRecordsResponse
from fastapi import status


def create_records_router(tool: AppTools) -> APIRouter:
    router = APIRouter(
        prefix=f"/instances/{{instance_uuid}}/templates/{{template_uuid}}/{tool.value}",
        tags=[tool.value.capitalize()],
    )

    @router.post("", response_model=RecordResponse, status_code=status.HTTP_201_CREATED)
    async def create_record(
        instance_uuid: UUID,
        template_uuid: UUID,
        payload: RecordCreateRequest,
        current_user=Depends(RequireTool(tool)),
        record_service: RecordService = Depends(get_record_service),
        s3_client=Depends(get_s3_client),
    ):
        # Никаких try/except! Если упадет TemplateNotFoundDomainError или RecordValidationError,
        # ошибка сама полетит вверх прямо в глобальный Exception Handler приложения.
        s3_service = S3StorageService(s3_client)

        return await record_service.create_new_record(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            user_uuid=current_user.uuid,
            data=payload.data,
            s3_service=s3_service,
        )

    @router.get("", response_model=PaginatedRecordsResponse)
    async def get_records(
        instance_uuid: UUID,
        template_uuid: UUID,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        sort_by: Optional[str] = Query(
            None, description="Имя поля внутри data для сортировки"
        ),
        descending: bool = Query(False, description="Сортировка по убыванию"),
        filters: Optional[str] = Query(
            None, description="JSON-строка фильтров. Например: {'age': {'$gt': 25}}"
        ),
        current_user=Depends(RequireTool(tool)),
        record_service: RecordService = Depends(get_record_service),
    ):
        # Парсим JSON-фильтры, если они переданы
        parsed_filters = {}
        if filters:
            try:
                parsed_filters = json.loads(filters)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=400, detail="Invalid JSON format in filters parameter"
                )

        # Передаем всё в сервис
        return await record_service.get_records_list(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            filters=parsed_filters,
            sort_by=sort_by,
            descending=descending,
            limit=limit,
            offset=offset,
        )

    @router.get("/export-csv")
    async def export_records_csv(
        instance_uuid: UUID,
        template_uuid: UUID,
        limit: int = Query(10000, ge=1, le=100000),
        sort_by: Optional[str] = Query(
            None, description="Имя поля внутри data для сортировки"
        ),
        descending: bool = Query(False, description="Сортировка по убыванию"),
        filters: Optional[str] = Query(
            None, description="JSON-строка фильтров. Например: {'age': {'$gt': 25}}"
        ),
        current_user=Depends(RequireTool(tool)),
        record_service: RecordService = Depends(get_record_service),
    ):
        """Выгрузка записей шаблона в CSV с теми же фильтрами, что и список."""
        parsed_filters = {}
        if filters:
            try:
                parsed_filters = json.loads(filters)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=400, detail="Invalid JSON format in filters parameter"
                )

        csv_content = await record_service.export_records_to_csv(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            filters=parsed_filters,
            sort_by=sort_by,
            descending=descending,
            limit=limit,
        )
        return Response(
            content=csv_content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{tool.value}-{template_uuid}.csv"'
                )
            },
        )

    @router.post(
        "/import-csv",
        response_model=CSVImportResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def import_records_csv(
        instance_uuid: UUID,
        template_uuid: UUID,
        payload: CSVImportRequest,
        current_user=Depends(RequireTool(tool)),
        record_service: RecordService = Depends(get_record_service),
        s3_client=Depends(get_s3_client),
    ):
        """Импорт записей шаблона из CSV: вносятся поля data по схеме,
        служебные поля создаются системой, каждая строка проходит полную
        валидацию записи."""
        s3_service = S3StorageService(s3_client)
        try:
            return await record_service.import_records_from_csv(
                instance_uuid=instance_uuid,
                template_uuid=template_uuid,
                user_uuid=current_user.uuid,
                csv_content=payload.csv_content,
                delimiter=payload.delimiter,
                s3_service=s3_service,
            )
        except CSVImportValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": "CSV не прошёл валидацию, ничего не создано",
                    "errors": exc.errors,
                },
            )

    @router.get("/deleted", response_model=PaginatedRecordsResponse)
    async def get_deleted_records(
        instance_uuid: UUID,
        template_uuid: UUID,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        sort_by: Optional[str] = Query(
            None, description="Имя поля внутри data для сортировки"
        ),
        descending: bool = Query(False, description="Сортировка по убыванию"),
        filters: Optional[str] = Query(
            None, description="JSON-строка фильтров. Например: {'age': {'$gt': 25}}"
        ),
        current_user=Depends(RequireTool(tool)),
        record_service: RecordService = Depends(get_record_service),
    ):
        parsed_filters = {}
        if filters:
            try:
                parsed_filters = json.loads(filters)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=400, detail="Invalid JSON format in filters parameter"
                )

        return await record_service.get_deleted_records_list(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            filters=parsed_filters,
            sort_by=sort_by,
            descending=descending,
            limit=limit,
            offset=offset,
        )

    @router.patch("/{record_uuid}", response_model=RecordResponse)
    async def update_record(
        instance_uuid: UUID,
        template_uuid: UUID,
        record_uuid: UUID,
        payload: RecordUpdateRequest,
        current_user=Depends(RequireTool(tool)),
        record_service: RecordService = Depends(get_record_service),
        s3_client=Depends(get_s3_client),
    ):
        s3_service = S3StorageService(s3_client)

        return await record_service.update_existing_record(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            record_uuid=record_uuid,
            user_uuid=current_user.uuid,
            new_data=payload.data,
            s3_service=s3_service,
        )

    @router.delete("/{record_uuid}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_record(
        instance_uuid: UUID,
        template_uuid: UUID,
        record_uuid: UUID,
        current_user=Depends(RequireTool(tool)),
        record_service: RecordService = Depends(get_record_service),
    ):
        await record_service.delete_record(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            record_uuid=record_uuid,
        )

    @router.post("/{record_uuid}/restore", response_model=RecordResponse)
    async def restore_record(
        instance_uuid: UUID,
        template_uuid: UUID,
        record_uuid: UUID,
        current_user=Depends(RequireTool(tool)),
        record_service: RecordService = Depends(get_record_service),
    ):
        return await record_service.restore_record(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            record_uuid=record_uuid,
        )

    return router

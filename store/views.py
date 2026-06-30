# store/views.py

from fastapi import APIRouter, Depends, Query, Request
from uuid import UUID
from jsonwebtoken.utils import get_current_user_optional
from policy.models import StorefrontPolicies  # Подтянули модель политик

from store.service import StorefrontService
from store.schemas import (
    StorefrontSchemaResponse,
    StorefrontRecordResponse,
    StorefrontRecordCreateRequest,
    StorefrontPaginatedRecordsResponse,
)
from core.dependencies import get_record_service
from store.dependecies import (
    get_active_instance_uuid,
    get_storefront_service,
    get_active_policy,
)
from store.utils import parse_query_filters

router = APIRouter(
    prefix="/storefront/{instance_title}/{template_name}",
    tags=["Storefront (Client API)"],
)


@router.get("/schema", response_model=StorefrontSchemaResponse)
async def get_client_schema(
    template_name: str,
    instance_uuid: UUID = Depends(get_active_instance_uuid),
    sf_service: StorefrontService = Depends(get_storefront_service),
    current_user=Depends(get_current_user_optional),
    policy: StorefrontPolicies = Depends(get_active_policy),  # 🔥 Защита включена
):
    """Отдает фронтенду магазина доступные поля таблицы для построения фильтров."""
    schema_dict = await sf_service.get_template_schema(instance_uuid, template_name)
    return StorefrontSchemaResponse(template_name=template_name, fields=schema_dict)


@router.get("/records", response_model=StorefrontPaginatedRecordsResponse)
async def search_records(
    request: Request,
    template_name: str,
    limit: int = Query(100, le=100),
    offset: int = Query(0, ge=0),
    instance_uuid: UUID = Depends(get_active_instance_uuid),
    sf_service: StorefrontService = Depends(get_storefront_service),
    current_user=Depends(get_current_user_optional),
    policy: StorefrontPolicies = Depends(get_active_policy),
):
    """
    Получение списка записей (каталог товаров витрины) с поддержкой пагинации.
    """
    filters = parse_query_filters(request)

    records_list, total_count = await sf_service.get_records(
        instance_uuid, template_name, filters, limit, offset
    )

    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "results": records_list,
    }


@router.post("/records", response_model=StorefrontRecordResponse)
async def create_client_record(
    template_name: str,
    payload: StorefrontRecordCreateRequest,
    instance_uuid: UUID = Depends(get_active_instance_uuid),
    sf_service: StorefrontService = Depends(get_storefront_service),
    record_service=Depends(get_record_service),
    current_user=Depends(get_current_user_optional),
    policy: StorefrontPolicies = Depends(get_active_policy),  # 🔥 Защита включена
):
    """Создание записи через витрину (заказы, лиды, формы обратной связи)."""
    # 1. Очищаем входящие данные по маске (write_mask) и получаем UUID шаблона в Mongo
    template_uuid, clean_data = await sf_service.prepare_create_payload(
        instance_uuid, template_name, payload.data
    )

    # 2. Определяем автора (если зашел гость, передаем пустой UUID)
    user_id_to_pass = current_user.uuid if current_user else UUID(int=0)

    # 3. Вызываем основной RecordService CRM для запуска формул AST и триггеров
    record = await record_service.create_new_record(
        instance_uuid=instance_uuid,
        template_uuid=UUID(template_uuid),
        user_uuid=user_id_to_pass,
        data=clean_data,
    )
    return record

# core/views/history.py

from fastapi import APIRouter, Depends
from uuid import UUID
from jsonwebtoken.utils import get_current_active_user
from users.models import Users
from core.schemas.history import FieldHistoryResponse
from core.schemas.history import FullHistoryResponse
from core.services.history import HistoryService
from core.dependencies import get_history_service

router = APIRouter(prefix="/history", tags=["history"])


@router.get("/field/{record_uuid}/{field_name}/", response_model=FieldHistoryResponse)
async def get_field_history_endpoint(
    record_uuid: UUID,
    field_name: str,
    current_user: Users = Depends(get_current_active_user),
    history_service: HistoryService = Depends(get_history_service),
):

    field_history = await history_service.get_field_history(
        current_user=current_user, record_uuid=record_uuid, field_name=field_name
    )

    return FieldHistoryResponse(
        status="success",
        record_uuid=record_uuid,
        field_name=field_name,
        history=field_history,
    )


@router.get("/record/{record_uuid}/", response_model=FullHistoryResponse)
async def get_full_record_history_endpoint(
    record_uuid: UUID,
    current_user: Users = Depends(get_current_active_user),
    history_service: HistoryService = Depends(get_history_service),
):
    """
    Эндпоинт получения ПОЛНОЙ истории изменений для конкретной записи (все снапшоты).
    Автоматически изолирует данные в рамках инстанса текущего пользователя.
    """
    full_history = await history_service.get_full_record_history(
        current_user=current_user, record_uuid=record_uuid
    )

    return FullHistoryResponse(
        status="success", record_uuid=record_uuid, history=full_history
    )

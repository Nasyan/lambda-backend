# users/views/client_api.py

from fastapi import APIRouter, Depends, Response, Cookie
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from redisdb.utils import get_redis_db
from users.schemas import (
    ClientRegisterRequest,  # Должна содержать: email, password, name, instance_id
    VerifyRegistrationRequest,
    ResendVerificationCodeRequest,
)
from users.schemas import ClientProfileResponse
from users.models import Users

from jsonwebtoken.utils import get_current_client
from users.services.client_auth_redis_service import ClientAuthRedisService
from users.services.client_auth_service import ClientAuthService

router = APIRouter(prefix="/storefront-auth", tags=["Client Auth (Public)"])


def get_client_auth_service(
    session: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis_db("EMAIL_DB")),
) -> ClientAuthService:
    redis_service = ClientAuthRedisService(redis_client)
    return ClientAuthService(session, redis_service)


@router.post("/register/")
async def register_client(
    payload: ClientRegisterRequest,
    service: ClientAuthService = Depends(get_client_auth_service),
):
    await service.register_client(payload)
    return {"status": "success", "message": "Verification code sent."}


@router.post("/verify/")
async def verify_client(
    payload: VerifyRegistrationRequest,
    service: ClientAuthService = Depends(get_client_auth_service),
):
    user = await service.verify_registration(payload)
    return {
        "status": "success",
        "user": {"email": user.email, "instance_id": str(user.instance_id)},
    }


@router.post("/resend-code/")
async def resend_code(
    payload: ResendVerificationCodeRequest,
    service: ClientAuthService = Depends(get_client_auth_service),
):
    await service.resend_code(payload)
    return {"status": "success", "message": "Code resent."}


@router.post("/login/")
async def login_client(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    service: ClientAuthService = Depends(get_client_auth_service),
):
    return await service.authenticate_and_issue_tokens(form_data, response)


@router.post("/refresh/")
async def refresh_client(
    response: Response,
    client_refresh_token: str | None = Cookie(default=None),
    service: ClientAuthService = Depends(get_client_auth_service),
):
    return await service.refresh_session_tokens(client_refresh_token, response)


@router.get("/me/", response_model=ClientProfileResponse)
async def get_client_profile(current_client: Users = Depends(get_current_client)):
    """
    Получение профиля текущего авторизованного клиента магазина.
    Доступ закрыт для ADMIN и CREATOR. Только для роли CLIENT.
    """
    # Благодаря response_model=ClientProfileResponse, Pydantic отфильтрует
    # объект модели Users и отдаст наружу ТОЛЬКО uuid, email, name и instance_id.
    return current_client

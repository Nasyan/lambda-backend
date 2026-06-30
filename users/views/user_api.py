# users/views/user_api.py

from fastapi import APIRouter, Depends, status, Response, Cookie
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from redisdb.utils import get_redis_db
from users.schemas import (
    ResendVerificationCodeRequest,
    UserRegisterRequest,
    VerifyRegistrationRequest,
)

from users.services.auth_service import AuthRedisService, AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(
    session: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis_db("EMAIL_DB")),
) -> AuthService:
    """Фабрика-провайдер для сборки сервиса авторизации."""
    redis_service = AuthRedisService(redis_client)
    return AuthService(session, redis_service)


@router.post("/register")
async def register_user(
    payload: UserRegisterRequest, auth_service: AuthService = Depends(get_auth_service)
):
    """Регистрация или обновление неактивного пользователя."""
    await auth_service.register_user(payload)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "status": "success",
            "message": "Account initialized successfully. Please check your email for verification code.",
        },
    )


@router.post("/verify-registration")
async def verify_registration(
    payload: VerifyRegistrationRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """Верификация кода активации."""
    user = await auth_service.verify_registration(payload)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "message": f"Account for {user.email} has been successfully activated.",
            "user": {
                "email": user.email,
                "name": user.name,
                "role": user.role.value,
                "instance_id": str(user.instance_id),
            },
        },
    )


@router.post("/resend-code")
async def resend_verification_code(
    payload: ResendVerificationCodeRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """Повторная отправка шестизначного кода активации (с защитой от спама)."""
    await auth_service.resend_code(payload)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "message": "A new verification code has been successfully sent to your email.",
        },
    )


@router.post("/login")
async def login_user(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Логин. Access-токен отдается в JSON, Refresh-токен прячется в HttpOnly Cookie."""
    return await auth_service.authenticate_and_issue_tokens(form_data, response)


@router.post("/refresh")
async def refresh_tokens(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    auth_service: AuthService = Depends(get_auth_service),
):
    """Ротация сессии: проверка старого Refresh-токена из куки и выпуск новой пары токенов."""
    return await auth_service.refresh_session_tokens(refresh_token, response)

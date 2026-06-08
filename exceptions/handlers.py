# exceptions/handlers.py

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from exceptions.base import BaseAppException
from store.exceptions import (
    StorefrontTemplateNotFoundError,
    StorefrontEmptyWritePayloadError,
)

from analytics.exceptions import (
    UnsupportedASTNodeError,
    WidgetNotFoundError,
    UnsupportedOperatorError,
    InvalidAggregationConfigError,
    AnalyticsCompilerException,
)
from core.exceptions.record import (
    TemplateNotFoundDomainError,
    RecordNotFoundDomainError,
    RecordValidationError as CoreRecordValidationError,
)
from core.exceptions.template import (
    TemplateMutationError,
    TemplateNotFoundException,
    DuplicateTemplateNameException,
)
from core.exceptions.permission import (
    PermissionsNotConfiguredError,
    ToolAccessDeniedError,
)
from core.exceptions.dependecies import (
    UserInactiveError,
    CreatorRoleRequiredError,
    InstanceAccessDeniedError,
    InstanceDeactivatedError,
    InstanceNotFoundError,
)
from engine.exceptions.evaluator import (
    FormulaDateFormatError,
    FormulaResolverRequiredError,
    FormulaValidationError,
    FormulaEvaluationError,
    FormulaTypeMismatchError,
)
from engine.exceptions.integrity import (
    CircularDependencyError,
    SchemaValidationError,
    SchemaDependencyError,
)

from jsonwebtoken.exceptions.utils import (
    CryptoKeyNotFoundError,
    InvalidTokenError,
    UserAccountNotFoundError,
    InsufficientPermissionsError,
    InstanceAssociationError,
)

from minio.exceptions.service import (
    StorageInfrastructureError,
    StorageFileNotFoundError,
    StorageURLGenerationError,
)

from mongo.exceptions.record import (
    RecordNotFoundError,
    RecordValidationError,
    DuplicateRecordKeyError,
)
from mongo.exceptions.template import (
    TemplateNotFoundError,
    SchemaMutationError,
    TemplateValidationError,
)

from notifications.exceptions.dispatcher import (
    NotificationValidationError,
    NotificationDispatchError,
    NotificationNotFoundError,
)

from policy.exceptions.service import (
    PolicyTemplateNotFoundError,
    PolicyAlreadyExistsError,
    PolicyNotFoundError,
)

from triggers.exceptions.action import (
    AutomationValidationError,
    AutomationExecutionError,
    SystemContractViolation,
    TriggerNotFoundDomainError,
)
from triggers.exceptions.validation import (
    RecordValidationError as TriggerRecordValidationError,
)
from triggers.exceptions.service import (
    AutomationActionNotFoundError,
    AutomationConditionEvaluationError,
)
from policy.exceptions.dependencies import CrossTenantAccessDeniedError

from users.exceptions.admin_service import (
    InstanceNotFoundError as USERInstanceNotFoundError,
    InstanceAlreadyExistsError,
    InstanceDeactivatedError as USERInstanceDeactivatedError,
    CreatorNotFoundError,
    UserAlreadyExistsError,
    CreatorAlreadyDeactivatedError,
    InvalidAdminCredentialsError,
)

from users.exceptions.auth_service import (
    AuthRateLimitExceededError,
    InvitationRequiredError,
    InvitationExpiredError,
    VerificationCodeExpiredError,
    InvalidVerificationCodeError,
    UserAlreadyRegisteredError,
    UserNotFoundError,
    InvalidCredentialsError,
    InvalidTokenCredentialsError,
    StorageDataCorruptedError,
)

from users.exceptions.client_auth_redis_service import (
    ClientAuthRateLimitExceededError,
    ClientVerificationCodeExpiredError,
    ClientInvalidVerificationCodeError,
)

from users.exceptions.client_auth_service import (
    StorefrontInstanceNotFoundError,
    ClientAlreadyRegisteredError,
    ClientNotFoundError,
    InvalidResendRequestError,
    InvalidClientCredentialsError,
    InvalidClientTokenSessionError,
)

from users.exceptions.creator_service import (
    TargetUserNotFoundError,
    InstanceAccessDeniedError as InstanceAccessDeniedErrorCreator,
    TargetUserAlreadyExistsError,
    UserRoleStateError,
    SelfManagementDeniedError,
    CreatorPermissionsUpdateError,
    CreatorDeactivationDeniedError,
    TargetUserAlreadyInactiveError,
    InfrastructureStorageError,
)

EXCEPTION_STATUS_MAPPING = {
    WidgetNotFoundError: status.HTTP_404_NOT_FOUND,
    # analitycs/builders.py
    UnsupportedASTNodeError: status.HTTP_400_BAD_REQUEST,
    UnsupportedOperatorError: status.HTTP_400_BAD_REQUEST,
    InvalidAggregationConfigError: status.HTTP_400_BAD_REQUEST,
    AnalyticsCompilerException: status.HTTP_400_BAD_REQUEST,
    # core/services/records
    CoreRecordValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    TemplateNotFoundDomainError: status.HTTP_404_NOT_FOUND,
    RecordNotFoundDomainError: status.HTTP_404_NOT_FOUND,
    # core/services/template
    DuplicateTemplateNameException: status.HTTP_409_CONFLICT,
    TemplateNotFoundException: status.HTTP_404_NOT_FOUND,
    TemplateMutationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # core/permission
    PermissionsNotConfiguredError: status.HTTP_403_FORBIDDEN,
    ToolAccessDeniedError: status.HTTP_403_FORBIDDEN,
    # core/dependencies
    UserInactiveError: status.HTTP_401_UNAUTHORIZED,
    CreatorRoleRequiredError: status.HTTP_403_FORBIDDEN,
    InstanceNotFoundError: status.HTTP_404_NOT_FOUND,
    InstanceDeactivatedError: status.HTTP_403_FORBIDDEN,
    InstanceAccessDeniedError: status.HTTP_403_FORBIDDEN,
    # engine/evaluator
    FormulaEvaluationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FormulaDateFormatError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FormulaResolverRequiredError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FormulaValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FormulaTypeMismatchError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # engine/schema_rules + core/services/template_integrity (бывш. engine/integrity)
    CircularDependencyError: status.HTTP_400_BAD_REQUEST,
    SchemaValidationError: status.HTTP_400_BAD_REQUEST,
    SchemaDependencyError: status.HTTP_409_CONFLICT,
    # jsonwebtoken/utils
    CryptoKeyNotFoundError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    InvalidTokenError: status.HTTP_401_UNAUTHORIZED,
    UserAccountNotFoundError: status.HTTP_401_UNAUTHORIZED,
    InsufficientPermissionsError: status.HTTP_403_FORBIDDEN,
    InstanceAssociationError: status.HTTP_400_BAD_REQUEST,
    # minio/service
    StorageInfrastructureError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    StorageFileNotFoundError: status.HTTP_404_NOT_FOUND,
    StorageURLGenerationError: status.HTTP_400_BAD_REQUEST,
    # mongo/record
    RecordNotFoundError: status.HTTP_404_NOT_FOUND,
    RecordValidationError: status.HTTP_400_BAD_REQUEST,
    DuplicateRecordKeyError: status.HTTP_409_CONFLICT,
    # mongo/template
    TemplateNotFoundError: status.HTTP_404_NOT_FOUND,
    SchemaMutationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    TemplateValidationError: status.HTTP_400_BAD_REQUEST,
    # notification/dispatcher
    NotificationValidationError: status.HTTP_400_BAD_REQUEST,
    NotificationDispatchError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    NotificationNotFoundError: status.HTTP_404_NOT_FOUND,
    # policy/service
    PolicyTemplateNotFoundError: status.HTTP_400_BAD_REQUEST,
    PolicyAlreadyExistsError: status.HTTP_400_BAD_REQUEST,
    PolicyNotFoundError: status.HTTP_404_NOT_FOUND,
    # policy/service
    CrossTenantAccessDeniedError: status.HTTP_403_FORBIDDEN,
    # triggers/action
    AutomationValidationError: status.HTTP_400_BAD_REQUEST,
    AutomationExecutionError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # Invariant breach: stage-2 validator should have prevented this.
    SystemContractViolation: status.HTTP_500_INTERNAL_SERVER_ERROR,
    TriggerNotFoundDomainError: status.HTTP_404_NOT_FOUND,
    TriggerRecordValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # triggers/service
    AutomationActionNotFoundError: status.HTTP_400_BAD_REQUEST,
    AutomationConditionEvaluationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # users/admin_service
    USERInstanceNotFoundError: status.HTTP_404_NOT_FOUND,
    CreatorNotFoundError: status.HTTP_404_NOT_FOUND,
    InstanceAlreadyExistsError: status.HTTP_400_BAD_REQUEST,
    USERInstanceDeactivatedError: status.HTTP_400_BAD_REQUEST,
    UserAlreadyExistsError: status.HTTP_400_BAD_REQUEST,
    CreatorAlreadyDeactivatedError: status.HTTP_400_BAD_REQUEST,
    InvalidAdminCredentialsError: status.HTTP_401_UNAUTHORIZED,
    # users/auth_service
    InvitationRequiredError: status.HTTP_403_FORBIDDEN,
    InvitationExpiredError: status.HTTP_403_FORBIDDEN,
    AuthRateLimitExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    VerificationCodeExpiredError: status.HTTP_400_BAD_REQUEST,
    InvalidVerificationCodeError: status.HTTP_400_BAD_REQUEST,
    UserAlreadyRegisteredError: status.HTTP_400_BAD_REQUEST,
    InvalidCredentialsError: status.HTTP_400_BAD_REQUEST,
    UserNotFoundError: status.HTTP_404_NOT_FOUND,
    InvalidTokenCredentialsError: status.HTTP_401_UNAUTHORIZED,
    StorageDataCorruptedError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    # users/client_auth_redis_service
    ClientAuthRateLimitExceededError: status.HTTP_429_TOO_MANY_REQUESTS,
    ClientVerificationCodeExpiredError: status.HTTP_400_BAD_REQUEST,
    ClientInvalidVerificationCodeError: status.HTTP_400_BAD_REQUEST,
    # users/client_auth_service
    StorefrontInstanceNotFoundError: status.HTTP_404_NOT_FOUND,
    ClientNotFoundError: status.HTTP_404_NOT_FOUND,
    ClientAlreadyRegisteredError: status.HTTP_400_BAD_REQUEST,
    InvalidResendRequestError: status.HTTP_400_BAD_REQUEST,
    InvalidClientCredentialsError: status.HTTP_400_BAD_REQUEST,
    InvalidClientTokenSessionError: status.HTTP_401_UNAUTHORIZED,
    # users/creator_service
    TargetUserNotFoundError: status.HTTP_404_NOT_FOUND,
    InstanceAccessDeniedErrorCreator: status.HTTP_403_FORBIDDEN,
    TargetUserAlreadyExistsError: status.HTTP_400_BAD_REQUEST,
    UserRoleStateError: status.HTTP_400_BAD_REQUEST,
    SelfManagementDeniedError: status.HTTP_400_BAD_REQUEST,
    CreatorPermissionsUpdateError: status.HTTP_400_BAD_REQUEST,
    CreatorDeactivationDeniedError: status.HTTP_400_BAD_REQUEST,
    TargetUserAlreadyInactiveError: status.HTTP_400_BAD_REQUEST,
    InfrastructureStorageError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    StorefrontTemplateNotFoundError: status.HTTP_404_NOT_FOUND,
    StorefrontEmptyWritePayloadError: status.HTTP_400_BAD_REQUEST,
}


def resolve_exception_status_code(exc: BaseAppException) -> int:
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        return int(status_code)

    for exc_class, mapped_status in EXCEPTION_STATUS_MAPPING.items():
        if isinstance(exc, exc_class):
            return mapped_status

    return status.HTTP_500_INTERNAL_SERVER_ERROR


async def app_exception_handler(
    request: Request, exc: BaseAppException
) -> JSONResponse:
    """Перехватчик контролируемых бизнес-исключений платформы."""
    status_code = resolve_exception_status_code(exc)

    return JSONResponse(
        status_code=status_code,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
        },
    )


async def universal_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Глобальный "улавливатель" непредвиденных системных падений (Panic/500)."""

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error_code": "INTERNAL_SERVER_ERROR",
            "message": "На сервере произошла критическая ошибка. Инженеры уже уведомлены.",
            "details": {},
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Функция-регистратор для красивого подключения в main.py."""
    app.add_exception_handler(BaseAppException, app_exception_handler)
    app.add_exception_handler(Exception, universal_exception_handler)

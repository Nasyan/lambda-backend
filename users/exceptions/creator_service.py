# users/exceptions/creator_service.py

from uuid import UUID
from exceptions.base import BaseAppException


class CreatorServiceException(BaseAppException):
    """Базовое исключение для бизнес-логики управления сотрудниками (Creator Service)."""

    error_code = "CREATOR_SERVICE_ERROR"


# --- Нарушение безопасности и изоляции данных ---
class TargetUserNotFoundError(CreatorServiceException):
    """Выбрасывается, если целевой пользователь отсутствует в системе."""

    status_code = 404
    error_code = "TARGET_USER_NOT_FOUND"
    message = "Запрашиваемый пользователь не найден."

    def __init__(self, target_uuid: UUID):
        super().__init__(
            message=self.message, details={"target_uuid": str(target_uuid)}
        )


class InstanceAccessDeniedError(CreatorServiceException):
    """Выбрасывается при попытке управлять пользователем из чужого инстанса (пространства)."""

    status_code = 403
    error_code = "INSTANCE_ACCESS_DENIED"
    message = "Доступ запрещен. Вы можете управлять пользователями только своего пространства."

    def __init__(
        self, target_uuid: UUID, target_instance_id: UUID, creator_instance_id: UUID
    ):
        super().__init__(
            message=self.message,
            details={
                "target_uuid": str(target_uuid),
                "target_instance_id": str(target_instance_id),
                "creator_instance_id": str(creator_instance_id),
            },
        )


# --- Ошибки валидации ролей и действий над собой ---
class TargetUserAlreadyExistsError(CreatorServiceException):
    """Выбрасывается при попытке пригласить уже существующего пользователя."""

    status_code = 400
    error_code = "TARGET_USER_ALREADY_EXISTS"
    message = "Пользователь с таким email-адресом уже зарегистрирован в системе."

    def __init__(self, email: str):
        super().__init__(message=self.message, details={"email": email})


class UserRoleStateError(CreatorServiceException):
    """Выбрасывается, если пользователь уже находится в целевой роли (уже создатель или уже обычный юзер)."""

    status_code = 400
    error_code = "USER_ROLE_STATE_ERROR"

    def __init__(self, target_uuid: UUID, current_role: str, action: str):
        super().__init__(
            message=f"Невозможно выполнить действие '{action}': пользователь уже имеет роль {current_role}.",
            details={
                "target_uuid": str(target_uuid),
                "current_role": current_role,
                "action": action,
            },
        )


class SelfManagementDeniedError(CreatorServiceException):
    """Выбрасывается при попытке понизить в роли или деактивировать самого себя."""

    status_code = 400
    error_code = "SELF_MANAGEMENT_DENIED"

    def __init__(self, creator_uuid: UUID, action: str):
        super().__init__(
            message=f"Запрещено выполнять операцию '{action}' над собственным аккаунтом.",
            details={"creator_uuid": str(creator_uuid), "action": action},
        )


class CreatorPermissionsUpdateError(CreatorServiceException):
    """Выбрасывается при попытке точечно обновить права аккаунту с ролью CREATOR."""

    status_code = 400
    error_code = "CREATOR_PERMISSIONS_UPDATE_DENIED"
    message = "Невозможно изменить точечные права для аккаунта Создателя. Создатели автоматически обладают полным доступом."

    def __init__(self, target_uuid: UUID):
        super().__init__(
            message=self.message, details={"target_uuid": str(target_uuid)}
        )


class CreatorDeactivationDeniedError(CreatorServiceException):
    """Выбрасывается при попытке деактивировать активного Создателя без предварительного понижения роли."""

    status_code = 400
    error_code = "CREATOR_DEACTIVATION_DENIED"
    message = "Невозможно деактивировать аккаунт Создателя. Сначала необходимо понизить его роль до обычного пользователя."

    def __init__(self, target_uuid: UUID):
        super().__init__(
            message=self.message, details={"target_uuid": str(target_uuid)}
        )


class TargetUserAlreadyInactiveError(CreatorServiceException):
    """Выбрасывается, если пользователь уже деактивирован."""

    status_code = 400
    error_code = "TARGET_USER_ALREADY_INACTIVE"
    message = "Этот пользователь уже деактивирован."

    def __init__(self, target_uuid: UUID):
        super().__init__(
            message=self.message, details={"target_uuid": str(target_uuid)}
        )


# --- Инфраструктурные сбои (Системные) ---
class InfrastructureStorageError(CreatorServiceException):
    """Выбрасывается при падении транзакций БД или сбоях связи с кэшем Redis."""

    status_code = 500
    error_code = "INFRASTRUCTURE_STORAGE_ERROR"
    message = "Внутренний сбой при сохранении данных в хранилище платформы."

    def __init__(self, context_message: str, reason: str):
        super().__init__(
            message=f"{self.message} Контекст: {context_message}.",
            details={"context": context_message, "reason": reason},
        )


class CreatorRoleRequiredError(CreatorServiceException):
    status_code = 403
    error_code = "CREATOR_ROLE_REQUIRED"
    message = "Данное действие доступно только создателям инстансов (CREATOR)."


class InstanceNotFoundError(CreatorServiceException):
    status_code = 404
    error_code = "INSTANCE_NOT_FOUND"
    message = "Запрашиваемый рабочий контур (инстанс) не найден."

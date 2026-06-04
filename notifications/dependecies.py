from uuid import UUID
from users.models import Users, UserRole
from users.exceptions.creator_service import (
    CreatorRoleRequiredError,
    InstanceNotFoundError,
    InstanceAccessDeniedError,
)


def verify_creator_and_instance(instance_uuid: UUID, current_user: Users) -> None:
    """Бизнес-валидация прав Креатора и изоляция инстанса."""
    if current_user.role != UserRole.CREATOR:
        raise CreatorRoleRequiredError()

    if not current_user.instance_id:
        raise InstanceNotFoundError(
            detail="Creator account is not associated with any active instance."
        )

    if current_user.instance_id != instance_uuid:
        raise InstanceAccessDeniedError(
            user_uuid=str(current_user.uuid),
            user_instance_id=str(current_user.instance_id),
            target_instance_uuid=str(instance_uuid),
        )


def verify_user_instance(instance_uuid: UUID, current_user: Users) -> None:
    if not current_user.instance_id:
        raise InstanceNotFoundError(
            detail="User account is not associated with any active instance."
        )

    if current_user.instance_id != instance_uuid:
        raise InstanceAccessDeniedError(
            user_uuid=str(current_user.uuid),
            user_instance_id=str(current_user.instance_id),
            target_instance_uuid=str(instance_uuid),
        )

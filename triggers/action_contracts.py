from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional

from triggers.models import PayloadReturnType


@dataclass(frozen=True)
class ActionSignature:
    action_name: str
    accepted_payload_types: FrozenSet[PayloadReturnType]
    requires_action_mapping_ast: bool = False


def _types(*values: PayloadReturnType) -> FrozenSet[PayloadReturnType]:
    return frozenset(values)


VALUE_OR_LIST = _types(PayloadReturnType.VALUE, PayloadReturnType.LIST)
ANY_PAYLOAD = _types(
    PayloadReturnType.BOOLEAN,
    PayloadReturnType.VALUE,
    PayloadReturnType.LIST,
)


ACTION_SIGNATURES: Dict[str, ActionSignature] = {
    "RETURN_TO_CALLER": ActionSignature("RETURN_TO_CALLER", ANY_PAYLOAD),
    "test_action": ActionSignature("test_action", ANY_PAYLOAD),
    "create_crm_notification": ActionSignature(
        "create_crm_notification", VALUE_OR_LIST
    ),
    "SEND_NOTIFICATION": ActionSignature("SEND_NOTIFICATION", VALUE_OR_LIST),
    "send_telegram_broadcast": ActionSignature(
        "send_telegram_broadcast", _types(PayloadReturnType.LIST)
    ),
    "SEND_BULK_NOTIFICATION": ActionSignature(
        "SEND_BULK_NOTIFICATION", _types(PayloadReturnType.LIST)
    ),
    "mongo_insert": ActionSignature(
        "mongo_insert", VALUE_OR_LIST, requires_action_mapping_ast=True
    ),
    "INSERT_RECORD": ActionSignature(
        "INSERT_RECORD", VALUE_OR_LIST, requires_action_mapping_ast=True
    ),
    "mongo_update": ActionSignature(
        "mongo_update", VALUE_OR_LIST, requires_action_mapping_ast=True
    ),
    "UPDATE_RECORD": ActionSignature(
        "UPDATE_RECORD", VALUE_OR_LIST, requires_action_mapping_ast=True
    ),
    "mongo_upsert": ActionSignature(
        "mongo_upsert", VALUE_OR_LIST, requires_action_mapping_ast=True
    ),
    "UPSERT_RECORD": ActionSignature(
        "UPSERT_RECORD", VALUE_OR_LIST, requires_action_mapping_ast=True
    ),
}


DML_ACTION_NAMES = frozenset(
    {
        "mongo_insert",
        "INSERT_RECORD",
        "mongo_update",
        "UPDATE_RECORD",
        "mongo_upsert",
        "UPSERT_RECORD",
    }
)


def get_action_signature(action_name: Optional[str]) -> Optional[ActionSignature]:
    if not action_name:
        return None
    return ACTION_SIGNATURES.get(action_name)

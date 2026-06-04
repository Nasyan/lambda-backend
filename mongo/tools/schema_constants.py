# mongo/tools/schema_constants.py

from re import compile

# mongo/tools/schema_constants.py

ALLOWED_META_KEYS = {
    "type",
    "required",
    "default",
    "indexed",
    "unique",
    "nullable",
    "description",
    "options",
    "ast",
    "target_template_uuid",
    "triggers",
    "tree_config",
    "ui_widget",  # 🔥 Добавили поддержку
}

# Белый список допустимых значений
ALLOWED_UI_WIDGETS = {
    "qr",
    "camera_capture",
    "file_upload",
    "geo_point",
    "phone_mask",
    "color_picker",
}

RESERVED_FIELD_NAMES = {
    "_id",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "schema",
    "instance_uuid",
}

FIELD_NAME_REGEX = compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")

MAX_SCHEMA_FIELDS = 1024

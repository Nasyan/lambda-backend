import os
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - exercised only without dependency

    def load_dotenv(*args, **kwargs) -> bool:
        return False


BASE_DIR = Path(__file__).resolve().parent.parent
dotenv_path = BASE_DIR / ".env"
load_dotenv(dotenv_path=dotenv_path)


class AppStage(str, Enum):
    DEV_LOCAL = "DEV_LOCAL"
    DEV_CONTAINER_BACK = "DEV_CONTAINER_BACK"
    DEV_FULL_COMPOSE = "DEV_FULL_COMPOSE"


APP_MODE_ALIASES = {
    "dev": AppStage.DEV_LOCAL,
    "local": AppStage.DEV_LOCAL,
    "dev_local": AppStage.DEV_LOCAL,
    "container": AppStage.DEV_CONTAINER_BACK,
    "container_back": AppStage.DEV_CONTAINER_BACK,
    "dev_container_back": AppStage.DEV_CONTAINER_BACK,
    "docker": AppStage.DEV_CONTAINER_BACK,
    "docker_dev": AppStage.DEV_CONTAINER_BACK,
    "compose": AppStage.DEV_FULL_COMPOSE,
    "full_compose": AppStage.DEV_FULL_COMPOSE,
    "dev_full_compose": AppStage.DEV_FULL_COMPOSE,
    "prod": AppStage.DEV_FULL_COMPOSE,
    "production": AppStage.DEV_FULL_COMPOSE,
}


def resolve_app_stage(raw_mode: Optional[str]) -> AppStage:
    normalized_mode = (raw_mode or AppStage.DEV_LOCAL.value).strip().strip("\"'")
    alias_key = normalized_mode.lower().replace("-", "_")

    if normalized_mode in AppStage._value2member_map_:
        return AppStage(normalized_mode)
    if alias_key in APP_MODE_ALIASES:
        return APP_MODE_ALIASES[alias_key]

    allowed_modes = ", ".join(stage.value for stage in AppStage)
    raise ValueError(
        f"Unsupported APP_MODE '{normalized_mode}'. Expected one of: {allowed_modes}."
    )


APP_MODE = os.getenv("APP_MODE", AppStage.DEV_LOCAL.value)
STAGE = resolve_app_stage(APP_MODE)
MODE = STAGE.value


def require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def require_int_env(name: str) -> int:
    value = require_env(name)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer") from exc


def is_local_stage() -> bool:
    return STAGE == AppStage.DEV_LOCAL


def resolve_service_host(env_name: str) -> str:
    return "127.0.0.1" if is_local_stage() else require_env(env_name)


def resolve_service_port(
    internal_env_name: str, external_env_name: Optional[str]
) -> str:
    if is_local_stage() and external_env_name:
        return require_env(external_env_name)
    return require_env(internal_env_name)


# --- Postgres ---
POSTGRES_DB_USER = require_env("POSTGRES_DB_USER")
POSTGRES_DB_PASSWORD = require_env("POSTGRES_DB_PASSWORD")
POSTGRES_DB_HOST = resolve_service_host("POSTGRES_DB_HOST")
POSTGRES_DB_PORT = require_env("POSTGRES_DB_PORT")
POSTGRES_DB_NAME = require_env("POSTGRES_DB_NAME")

# --- Postgres Test ---
POSTGRES_TEST_DB_NAME = require_env("POSTGRES_TEST_DB_NAME")
POSTGRES_TEST_DB_HOST = resolve_service_host("POSTGRES_TEST_DB_HOST")
POSTGRES_TEST_DB_PORT = require_env("POSTGRES_TEST_DB_PORT")

# --- Redis ---
REDIS_HOST = resolve_service_host("REDIS_HOST")
REDIS_PORT = resolve_service_port("REDIS_PORT", "REDIS_EXTERNAL_PORT")
REDIS_TEST_PORT = require_env("REDIS_TEST_PORT")

EMAIL_DB = require_int_env("EMAIL_DB")
TASK_DB = require_int_env("TASK_DB")
REGISTRATION_DB = require_int_env("REGISTRATION_DB")
LOGIN_DB = require_int_env("LOGIN_DB")
SET_ACTIVITY = require_env("SET_ACTIVITY")
RESET_PREFIX = require_env("RESET_PREFIX")
JOIN_PREFIX = require_env("JOIN_PREFIX")
INVITE_PREFIX = require_env("INVITE_PREFIX")
USER_INVITE_PREFIX = require_env("USER_INVITE_PREFIX")

# --- MongoDB ---
MONGO_HOST = resolve_service_host("MONGO_HOST")
MONGO_PORT = resolve_service_port("MONGO_PORT", "MONGO_EXTERNAL_PORT")
MONGO_DB_NAME = require_env("MONGO_DB_NAME")
MONGO_TEST_PORT = require_env("MONGO_TEST_PORT")

# --- MinIO (S3) ---
MINIO_HOST = resolve_service_host("MINIO_HOST")
MINIO_PORT = resolve_service_port("MINIO_PORT", "MINIO_EXTERNAL_PORT")
MINIO_CONSOLE_PORT = require_env("MINIO_CONSOLE_PORT")
MINIO_TEST_PORT = require_env("MINIO_TEST_PORT")
MINIO_ROOT_USER = require_env("MINIO_ROOT_USER")
MINIO_ROOT_PASSWORD = require_env("MINIO_ROOT_PASSWORD")
MINIO_DEFAULT_BUCKET = os.getenv("MINIO_DEFAULT_BUCKET", "lambda-media")

# --- Email & Admin ---
SENDER_EMAIL = require_env("SENDER_EMAIL")
EMAIL_PASSWORD = require_env("EMAIL_PASSWORD")
ADMIN_USERNAME = require_env("ADMIN_USERNAME")
ADMIN_PASSWORD = require_env("ADMIN_PASSWORD")

MONGO_URL = (
    f"mongodb://{ADMIN_USERNAME}:{ADMIN_PASSWORD}@{MONGO_HOST}:{MONGO_PORT}"
    "/?authSource=admin"
)

# --- MinIO URL Helper ---
MINIO_ENDPOINT_URL = f"http://{MINIO_HOST}:{MINIO_PORT}"

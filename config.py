import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
dotenv_path = BASE_DIR / ".env"
load_dotenv(dotenv_path=dotenv_path)

MODE = os.getenv("APP_MODE", "dev")


# --- Host taker ---
def get_host(env_name, default_local="127.0.0.1"):
    return default_local if MODE == "dev" else os.getenv(env_name)


# --- Postgres ---
POSTGRES_DB_USER = os.getenv("POSTGRES_DB_USER")
POSTGRES_DB_PASSWORD = os.getenv("POSTGRES_DB_PASSWORD")
POSTGRES_DB_HOST = get_host("POSTGRES_DB_HOST")
POSTGRES_DB_PORT = os.getenv("POSTGRES_DB_PORT")
POSTGRES_DB_NAME = os.getenv("POSTGRES_DB_NAME")

# --- Postgres Test ---
POSTGRES_TEST_DB_NAME = os.getenv("POSTGRES_TEST_DB_NAME")
POSTGRES_TEST_DB_HOST = get_host("POSTGRES_TEST_DB_HOST")
POSTGRES_TEST_DB_PORT = os.getenv("POSTGRES_TEST_DB_PORT")

# --- Redis ---
REDIS_HOST = get_host("REDIS_HOST")
REDIS_PORT = (
    os.getenv("REDIS_EXTERNAL_PORT") if MODE == "dev" else os.getenv("REDIS_PORT")
)
REDIS_TEST_PORT = os.getenv("REDIS_TEST_PORT")

EMAIL_DB = int(os.getenv("EMAIL_DB"))
TASK_DB = int(os.getenv("TASK_DB"))
REGISTRATION_DB = int(os.getenv("REGISTRATION_DB"))
LOGIN_DB = int(os.getenv("LOGIN_DB"))
SET_ACTIVITY = os.getenv("SET_ACTIVITY")
RESET_PREFIX = os.getenv("RESET_PREFIX")
JOIN_PREFIX = os.getenv("JOIN_PREFIX")
INVITE_PREFIX = os.getenv("INVITE_PREFIX")
USER_INVITE_PREFIX = os.getenv("USER_INVITE_PREFIX")

# --- MongoDB ---
MONGO_HOST = get_host("MONGO_HOST")
MONGO_PORT = (
    os.getenv("MONGO_EXTERNAL_PORT") if MODE == "dev" else os.getenv("MONGO_PORT")
)
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME")
MONGO_TEST_PORT = os.getenv("MONGO_TEST_PORT")
MONDO_DB_PASSWORD = os.getenv("MONDO_DB_PASSWORD")
MONGO_TEST_NAME = os.getenv("MONGO_TEST_NAME")

# --- MinIO (S3) ---
MINIO_HOST = get_host("MINIO_HOST")
MINIO_PORT = (
    os.getenv("MINIO_EXTERNAL_PORT") if MODE == "dev" else os.getenv("MINIO_PORT")
)
MINIO_CONSOLE_PORT = os.getenv("MINIO_CONSOLE_PORT")
MINIO_TEST_PORT = os.getenv("MINIO_TEST_PORT")
MINIO_ROOT_USER = os.getenv("MINIO_ROOT_USER")
MINIO_ROOT_PASSWORD = os.getenv("MINIO_ROOT_PASSWORD")
MINIO_DEFAULT_BUCKET = os.getenv("MINIO_DEFAULT_BUCKET", "lambda-media")

# --- Email & Admin ---
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

MONGO_URL = f"mongodb://{ADMIN_USERNAME}:{ADMIN_PASSWORD}@{MONGO_HOST}:{MONGO_PORT}/?authSource=admin"

# --- MinIO URL Helper ---
MINIO_ENDPOINT_URL = f"http://{MINIO_HOST}:{MINIO_PORT}"

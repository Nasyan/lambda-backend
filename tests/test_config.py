import importlib.util
from pathlib import Path

import pytest


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.py"

BASE_ENV = {
    "POSTGRES_DB_HOST": "postgres",
    "POSTGRES_DB_PORT": "5432",
    "POSTGRES_DB_USER": "postgres_user",
    "POSTGRES_DB_PASSWORD": "postgres_password",
    "POSTGRES_DB_NAME": "db",
    "POSTGRES_TEST_DB_HOST": "test-postgres",
    "POSTGRES_TEST_DB_PORT": "5433",
    "POSTGRES_TEST_DB_NAME": "test_db",
    "SENDER_EMAIL": "sender@example.com",
    "EMAIL_PASSWORD": "email_password",
    "REDIS_HOST": "redis",
    "REDIS_PORT": "6379",
    "REDIS_EXTERNAL_PORT": "16379",
    "REDIS_TEST_PORT": "6381",
    "EMAIL_DB": "0",
    "TASK_DB": "1",
    "REGISTRATION_DB": "2",
    "LOGIN_DB": "3",
    "JOIN_PREFIX": "100",
    "RESET_PREFIX": "200",
    "SET_ACTIVITY": "300",
    "INVITE_PREFIX": "400",
    "USER_INVITE_PREFIX": "500",
    "MONGO_HOST": "mongodb",
    "MONGO_PORT": "27017",
    "MONGO_EXTERNAL_PORT": "37017",
    "MONGO_TEST_PORT": "27018",
    "MONGO_DB_NAME": "lambda_db",
    "MINIO_HOST": "minio",
    "MINIO_PORT": "9000",
    "MINIO_CONSOLE_PORT": "9001",
    "MINIO_EXTERNAL_PORT": "19000",
    "MINIO_TEST_PORT": "9002",
    "MINIO_ROOT_USER": "minio_admin",
    "MINIO_ROOT_PASSWORD": "minio_password",
    "MINIO_DEFAULT_BUCKET": "lambda-media",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "admin_password",
}


def load_config_module(monkeypatch: pytest.MonkeyPatch, app_mode: str):
    with monkeypatch.context() as patch:
        for key, value in BASE_ENV.items():
            patch.setenv(key, value)
        patch.setenv("APP_MODE", app_mode)

        module_name = f"_config_under_test_{app_mode.lower().replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, CONFIG_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module


@pytest.mark.parametrize("app_mode", ["DEV_LOCAL", "dev"])
def test_dev_local_resolves_external_service_ports(monkeypatch, app_mode):
    cfg = load_config_module(monkeypatch, app_mode)

    assert cfg.MODE == "DEV_LOCAL"
    assert cfg.POSTGRES_DB_HOST == "127.0.0.1"
    assert cfg.POSTGRES_TEST_DB_HOST == "127.0.0.1"
    assert cfg.REDIS_HOST == "127.0.0.1"
    assert cfg.REDIS_PORT == "16379"
    assert cfg.MONGO_HOST == "127.0.0.1"
    assert cfg.MONGO_PORT == "37017"
    assert cfg.MINIO_HOST == "127.0.0.1"
    assert cfg.MINIO_PORT == "19000"
    assert "127.0.0.1:37017" in cfg.MONGO_URL
    assert cfg.MINIO_ENDPOINT_URL == "http://127.0.0.1:19000"


@pytest.mark.parametrize("app_mode", ["DEV_CONTAINER_BACK", "DEV_FULL_COMPOSE"])
def test_container_stages_resolve_compose_service_names(monkeypatch, app_mode):
    cfg = load_config_module(monkeypatch, app_mode)

    assert cfg.MODE == app_mode
    assert cfg.POSTGRES_DB_HOST == "postgres"
    assert cfg.POSTGRES_TEST_DB_HOST == "test-postgres"
    assert cfg.REDIS_HOST == "redis"
    assert cfg.REDIS_PORT == "6379"
    assert cfg.MONGO_HOST == "mongodb"
    assert cfg.MONGO_PORT == "27017"
    assert cfg.MINIO_HOST == "minio"
    assert cfg.MINIO_PORT == "9000"
    assert "mongodb:27017" in cfg.MONGO_URL
    assert cfg.MINIO_ENDPOINT_URL == "http://minio:9000"


def test_unknown_app_mode_fails_fast(monkeypatch):
    with pytest.raises(ValueError, match="Unsupported APP_MODE"):
        load_config_module(monkeypatch, "staging")

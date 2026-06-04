# health/tests/test_api_health.py

import pytest
from httpx import AsyncClient


class TestHealth:
    @pytest.mark.asyncio
    async def test_check_pulse(self, test_client: AsyncClient):
        response = await test_client.get("/health/pulse/")
        assert response.status_code == 200
        assert response.json()["description"] == "Fastapi alive"

    @pytest.mark.asyncio
    async def test_check_db(self, test_client: AsyncClient):
        response = await test_client.get("/health/db/")
        assert response.status_code == 200
        assert response.json()["description"] == "Postgres alive"

    @pytest.mark.asyncio
    async def test_check_redis(self, test_client: AsyncClient):
        response = await test_client.get("/health/redis/")
        assert response.status_code == 200
        assert response.json()["description"] == "Redis alive"

    @pytest.mark.asyncio
    async def test_check_mongo(self, test_client: AsyncClient):
        response = await test_client.get("/health/mongo/")
        assert response.status_code == 200
        assert response.json()["description"] == "MongoDB alive"

    @pytest.mark.asyncio
    async def test_check_migrations(self, test_client: AsyncClient):
        response = await test_client.get("/health/migrations/")
        assert response.status_code == 200
        data = response.json()

        assert "tables" in data
        assert isinstance(data["tables"], list)
        assert len(data["tables"]) > 0

    @pytest.mark.asyncio
    async def test_check_minio(self, test_client: AsyncClient):
        response = await test_client.get("/health/minio/")
        assert response.status_code == 200
        assert response.json()["description"] == "MinIO alive"

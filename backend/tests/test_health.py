from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_db

API = "/api/v1"


async def test_liveness_returns_app_metadata(client: AsyncClient) -> None:
    response = await client.get(f"{API}/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"
    assert body["version"]


async def test_readiness_reports_503_when_database_unreachable(app: FastAPI, client: AsyncClient) -> None:
    class BrokenSession:
        async def execute(self, *args: object, **kwargs: object) -> None:
            raise OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))

    async def broken_db() -> AsyncIterator[BrokenSession]:
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db

    response = await client.get(f"{API}/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"database": False, "pgvector": False}
    # Redis is reported separately and doesn't decide readiness.
    assert set(body["services"]) == {"redis", "ingestion_workers", "ingestion_waiting"}


@pytest.mark.integration
async def test_readiness_with_real_database(client: AsyncClient, migrated_database: None) -> None:
    response = await client.get(f"{API}/health/ready")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ready",
        "checks": {"database": True, "pgvector": True},
        "services": {"redis": True, "ingestion_workers": 0, "ingestion_waiting": 0},
    }

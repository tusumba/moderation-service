import json

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


async def test_health(client: httpx2.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response.headers["X-Request-ID"]) == 32


async def test_request_id_is_taken_from_header(client: httpx2.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


async def test_unsafe_request_id_is_replaced(client: httpx2.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "bad value"})
    assert response.headers["X-Request-ID"] != "bad value"


async def test_access_log_is_json(
    capsys: pytest.CaptureFixture[str], client: httpx2.AsyncClient
) -> None:
    capsys.readouterr()
    response = await client.get("/health", headers={"X-Request-ID": "trace-1"})
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    access = [entry for entry in lines if entry["logger"] == "app.access"]
    assert access, lines
    assert access[-1]["service"] == "moderation"
    assert access[-1]["request_id"] == "trace-1"
    assert access[-1]["path"] == "/health"
    assert access[-1]["status"] == response.status_code
    assert access[-1]["level"] == "INFO"


async def test_v1_prefix_exists(client: httpx2.AsyncClient) -> None:
    assert (await client.get("/v1/unknown")).status_code == 404


def test_app_starts_and_stops_with_database(settings: Settings) -> None:
    with TestClient(create_app(settings)) as test_client:
        assert test_client.get("/health").status_code == 200

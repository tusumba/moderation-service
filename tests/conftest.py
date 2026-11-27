import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.rabbitmq import RabbitMqContainer
from tusumba_common import InternalClient, PermissionsClient
from tusumba_common.testing import generate_keypair

from app.api.deps import get_permissions
from app.clients.events import EventsClient, get_events_client
from app.db import create_engine, get_session
from app.main import create_app
from app.settings import Settings

SERVICE_DIR = Path(__file__).resolve().parents[1]
POSTGRES_IMAGE = "postgres:16-alpine"
RABBITMQ_IMAGE = "rabbitmq:3.13-alpine"

SERVICE_TOKEN = "test-service-token"  # noqa: S105 - тестовое значение, не секрет


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    # TEST_DATABASE_URL позволяет прогнать тесты без Docker на уже запущенном PostgreSQL.
    if existing := os.environ.get("TEST_DATABASE_URL"):
        yield existing
        return
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def rabbitmq_url() -> Iterator[str]:
    if existing := os.environ.get("TEST_RABBITMQ_URL"):
        yield existing
        return
    with RabbitMqContainer(RABBITMQ_IMAGE) as rabbitmq:
        params = rabbitmq.get_connection_params()
        yield f"amqp://guest:guest@{params.host}:{params.port}/"


@pytest.fixture(scope="session")
def keypair() -> tuple[str, str]:
    return generate_keypair()


@pytest.fixture(autouse=True)
def common_env(keypair: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Переменные, которые tusumba-common читает из окружения процесса."""
    monkeypatch.setenv("JWT_PUBLIC_KEY", keypair[1])
    monkeypatch.setenv("SERVICE_TOKEN", SERVICE_TOKEN)


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    config = Config(SERVICE_DIR / "alembic.ini")
    config.set_main_option("script_location", str(SERVICE_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture
def settings(database_url: str, rabbitmq_url: str, migrated: None) -> Settings:
    return Settings(
        service_name="moderation",
        environment="test",
        database_url=database_url,
        rabbitmq_url=rabbitmq_url,
    )


@pytest.fixture
async def session(settings: Settings) -> AsyncIterator[AsyncSession]:
    engine = create_engine(settings)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as db_session:
            yield db_session
        await transaction.rollback()
    await engine.dispose()


@dataclass
class FakeEvents:
    """events-service для тестов, ответы по OpenAPI Events.

    - GET /internal/clubs/{id}/permissions: секции нет в clubs - 404 club_not_found
      (так же events-service отвечает и для заблокированной секции);
    - GET /internal/events/{id}, GET /internal/clubs/{id}: объекты из targets с текущей версией.
    """

    clubs: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=dict)
    targets: dict[tuple[str, uuid.UUID], dict[str, str]] = field(default_factory=dict)
    failure: Exception | int | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def add_club(self, *admins: uuid.UUID) -> uuid.UUID:
        club_id = uuid.uuid4()
        self.clubs[club_id] = set(admins)
        return club_id

    def add_target(self, kind: str, status: str = "published") -> tuple[uuid.UUID, uuid.UUID]:
        """Мероприятие (kind = events) или секция (clubs); возвращает id и текущую версию."""
        target_id, version_id = uuid.uuid4(), uuid.uuid4()
        self.targets[kind, target_id] = {
            "id": str(target_id),
            "current_version_id": str(version_id),
            "status": status,
        }
        return target_id, version_id

    def new_version(self, kind: str, target_id: uuid.UUID) -> uuid.UUID:
        """Отредактировать объект: у него появляется новая текущая версия."""
        version_id = uuid.uuid4()
        self.targets[kind, target_id]["current_version_id"] = str(version_id)
        return version_id

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.failure, Exception):
            raise self.failure
        if self.failure is not None:
            return httpx.Response(self.failure)
        if request.headers.get("X-Service-Token") != SERVICE_TOKEN:
            return httpx.Response(403, json={"error": {"code": "forbidden", "message": "-"}})
        _, kind, object_id, *rest = request.url.path.removeprefix("/").split("/")
        if rest == ["permissions"]:
            return self._club_permissions(uuid.UUID(object_id), request.url.params["user_id"])
        target = self.targets.get((kind, uuid.UUID(object_id)))
        if target is None:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "-"}})
        return httpx.Response(200, json=target)

    def _club_permissions(self, club_id: uuid.UUID, user_id: str) -> httpx.Response:
        admins = self.clubs.get(club_id)
        if admins is None:
            error = {"code": "club_not_found", "message": "Секция недоступна."}
            return httpx.Response(404, json={"error": error})
        return httpx.Response(
            200,
            json={
                "club_id": str(club_id),
                "user_id": user_id,
                "is_admin": uuid.UUID(user_id) in admins,
            },
        )


@pytest.fixture
def events(common_env: None) -> FakeEvents:
    return FakeEvents()


def events_http(events: FakeEvents) -> InternalClient:
    # InternalClient сам добавляет X-Service-Token, как в работающем сервисе
    return InternalClient(base_url="http://events", transport=httpx.MockTransport(events.handle))


@pytest.fixture
async def permissions(events: FakeEvents) -> AsyncIterator[PermissionsClient]:
    client = PermissionsClient("http://events", client=events_http(events))
    yield client
    await client.aclose()


@pytest.fixture
async def events_client(events: FakeEvents) -> AsyncIterator[EventsClient]:
    client = EventsClient("http://events", client=events_http(events))
    yield client
    await client.aclose()


@pytest.fixture
def app(settings: Settings, permissions: PermissionsClient, events_client: EventsClient) -> FastAPI:
    app = create_app(settings)
    app.dependency_overrides[get_permissions] = lambda: permissions
    app.dependency_overrides[get_events_client] = lambda: events_client
    return app


@pytest.fixture
async def client(app: FastAPI, session: AsyncSession) -> AsyncIterator[httpx2.AsyncClient]:
    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = test_session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client

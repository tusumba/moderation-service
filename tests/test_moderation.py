"""Очередь заявок, карточка и решение модератора (#19050): сценарии из требований #18106, #18108."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from faststream.rabbit import RabbitBroker
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tusumba_common import ApiError
from tusumba_common.testing import make_token
from tusumba_contracts import (
    ApplicationApproved,
    ApplicationRejected,
    BaseEvent,
    EventEnvelope,
    parse_envelope,
)

from app.db import create_engine, create_sessionmaker
from app.events import publish_pending, subscribe_event
from app.models import Application, ModerationLogEntry, OutboxMessage
from app.moderation import service
from app.moderation.validation import Approve, Reject
from app.settings import Settings

COMMENT_REQUIRED = "Укажите комментарий к отказу."
ALREADY_PROCESSED = "Заявка уже обработана."

EVENT_DATA = {
    "title": "Посвящение в студенты ФИТ",
    "description": "Концерт, конкурсы и дискотека для первокурсников.",
    "date": "2026-12-20",
    "place_address": "Новосибирск, ул. Пирогова, 1, Большая актовая аудитория",
    "is_free": False,
    "price": {"amount": 30000, "currency": "RUB"},
}
CLUB_PAGE_DATA = {
    "name": "Клуб настольных игр",
    "description": "Играем в настолки по пятницам.",
    "meeting_place": "Новосибирск, ул. Пирогова, 1, ауд. 3107",
    "contacts": {"telegram": "@nsu_boardgames"},
}


@dataclass(frozen=True)
class User:
    id: uuid.UUID
    headers: dict[str, str]


@pytest.fixture
def make_user(keypair: tuple[str, str]) -> Callable[..., User]:
    def make(roles: list[str] | None = None) -> User:
        user_id = uuid.uuid4()
        token = make_token(keypair[0], user_id=str(user_id), roles=roles)
        return User(user_id, {"Authorization": f"Bearer {token}"})

    return make


@pytest.fixture
def moderator(make_user: Callable[..., User]) -> User:
    return make_user(["moderator"])


@pytest.fixture
def author(make_user: Callable[..., User]) -> User:
    return make_user()


def application(
    author_id: uuid.UUID, kind: str = "event", changes: dict[str, Any] | None = None, **fields: Any
) -> Application:
    payload = {**(EVENT_DATA if kind == "event" else CLUB_PAGE_DATA), **(changes or {})}
    return Application(type=kind, author_user_id=author_id, payload=payload, **fields)


async def saved(session: AsyncSession, *applications: Application) -> list[Application]:
    session.add_all(applications)
    await session.flush()
    return list(applications)


async def decide(
    client: httpx2.AsyncClient, user: User, application_id: uuid.UUID, body: Any
) -> httpx2.Response:
    return await client.post(
        f"/v1/moderation/applications/{application_id}/decision", json=body, headers=user.headers
    )


async def outbox_events(session: AsyncSession, application_id: uuid.UUID) -> list[BaseEvent]:
    rows = await session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.payload["payload"]["application_id"].astext == str(application_id)
        )
    )
    return [parse_envelope(row.payload).payload for row in rows]


async def log_entries(session: AsyncSession, application_id: uuid.UUID) -> list[ModerationLogEntry]:
    rows = await session.scalars(
        select(ModerationLogEntry).where(ModerationLogEntry.object_id == application_id)
    )
    return list(rows)


def assert_validation_error(
    response: httpx2.Response, message: str, details: list[dict[str, str]]
) -> None:
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == message
    assert error["details"] == details


# Доступ: только глобальная роль moderator (критерий приёмки 1)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/v1/moderation/applications", None),
        ("GET", "/v1/moderation/applications/{id}", None),
        ("POST", "/v1/moderation/applications/{id}/decision", {"decision": "approve"}),
    ],
)
async def test_moderation_requires_moderator_role(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    author: User,
    method: str,
    path: str,
    body: Any,
) -> None:
    [item] = await saved(session, application(author.id))
    url = path.format(id=item.id)

    anonymous = await client.request(method, url, json=body)
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "not_authenticated"

    forbidden = await client.request(method, url, json=body, headers=author.headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"

    await session.refresh(item)
    assert item.status == "on_review"


# Очередь заявок (#18106, шаги 4.1-4.2, п. 5.3)


async def test_queue_shows_applications_on_review_oldest_first(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    now = datetime.now(UTC)
    old_event, club_page, new_event, approved = await saved(
        session,
        application(author.id, created_at=now - timedelta(days=3)),
        application(author.id, "club_page", created_at=now - timedelta(days=2)),
        application(author.id, changes={"title": "Новое"}, created_at=now - timedelta(days=1)),
        application(author.id, status="approved", created_at=now - timedelta(days=4)),
    )

    response = await client.get("/v1/moderation/applications", headers=moderator.headers)
    assert response.status_code == 200, response.text
    page = response.json()
    assert [item["id"] for item in page["items"]] == [
        str(old_event.id),
        str(club_page.id),
        str(new_event.id),
    ]
    assert (page["total"], page["page"], page["size"]) == (3, 1, 20)
    assert str(approved.id) not in {item["id"] for item in page["items"]}


async def test_queue_shows_both_application_types(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    event_item, club_item = await saved(
        session, application(author.id), application(author.id, "club_page")
    )
    items = (await client.get("/v1/moderation/applications", headers=moderator.headers)).json()[
        "items"
    ]
    by_id = {item["id"]: item for item in items}
    assert by_id[str(event_item.id)]["event"]["title"] == EVENT_DATA["title"]
    assert "club_page" not in by_id[str(event_item.id)]
    assert by_id[str(club_item.id)]["club_page"]["name"] == CLUB_PAGE_DATA["name"]
    assert "event" not in by_id[str(club_item.id)]


async def test_queue_filters_by_type_and_status(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    event_item, club_item, rejected = await saved(
        session,
        application(author.id),
        application(author.id, "club_page"),
        application(author.id, status="rejected", moderator_comment="Нет адреса"),
    )

    async def ids(**params: str) -> list[str]:
        response = await client.get(
            "/v1/moderation/applications", params=params, headers=moderator.headers
        )
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert await ids(type="club_page") == [str(club_item.id)]
    assert await ids(type="event") == [str(event_item.id)]
    assert await ids(status="rejected") == [str(rejected.id)]


async def test_queue_is_paginated(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    now = datetime.now(UTC)
    items = await saved(
        session,
        *(
            application(
                author.id,
                changes={"title": f"Мероприятие {i}"},
                created_at=now + timedelta(minutes=i),
            )
            for i in range(3)
        ),
    )
    response = await client.get(
        "/v1/moderation/applications", params={"page": 2, "size": 2}, headers=moderator.headers
    )
    page = response.json()
    assert [item["id"] for item in page["items"]] == [str(items[2].id)]
    assert (page["total"], page["page"], page["size"]) == (3, 2, 2)


async def test_empty_queue(client: httpx2.AsyncClient, moderator: User) -> None:
    response = await client.get("/v1/moderation/applications", headers=moderator.headers)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "size": 20}


@pytest.mark.parametrize(
    "params", [{"status": "draft"}, {"type": "club"}, {"page": "0"}, {"size": "101"}]
)
async def test_queue_rejects_invalid_params(
    client: httpx2.AsyncClient, moderator: User, params: dict[str, str]
) -> None:
    response = await client.get(
        "/v1/moderation/applications", params=params, headers=moderator.headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# Карточка заявки (#18106, шаги 4.3-4.4)


async def test_card_shows_application_and_author(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    [item] = await saved(session, application(author.id))
    response = await client.get(f"/v1/moderation/applications/{item.id}", headers=moderator.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(item.id)
    assert body["author_user_id"] == str(author.id)
    assert body["status"] == "on_review"
    assert body["event"] == EVENT_DATA


async def test_card_of_missing_application(client: httpx2.AsyncClient, moderator: User) -> None:
    response = await client.get(
        f"/v1/moderation/applications/{uuid.uuid4()}", headers=moderator.headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "application_not_found"


# Одобрение (#18106, шаги 4.6-4.9)


async def test_approve_event_application(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    [item] = await saved(session, application(author.id))

    response = await decide(client, moderator, item.id, {"decision": "approve"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["processed_by"] == str(moderator.id)
    assert body["processed_at"] is not None
    assert body["moderator_comment"] is None
    result_object_id = uuid.UUID(body["result_object_id"])

    [event] = await outbox_events(session, item.id)
    assert isinstance(event, ApplicationApproved)
    assert event.type == "event"
    assert event.author_user_id == author.id
    assert event.result_object_id == result_object_id
    assert event.processed_by == moderator.id
    assert event.club_page is None
    assert event.event is not None
    assert event.event.model_dump(mode="json") == EVENT_DATA

    [entry] = await log_entries(session, item.id)
    assert (entry.moderator_id, entry.action_type, entry.decision, entry.comment) == (
        moderator.id,
        "application",
        "approved",
        None,
    )


async def test_approve_club_page_application(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    [item] = await saved(session, application(author.id, "club_page"))

    response = await decide(client, moderator, item.id, {"decision": "approve"})
    assert response.status_code == 200, response.text

    [event] = await outbox_events(session, item.id)
    assert isinstance(event, ApplicationApproved)
    assert event.type == "club_page"
    assert event.event is None
    assert event.club_page is not None
    assert event.club_page.name == CLUB_PAGE_DATA["name"]
    assert event.club_page.contacts == CLUB_PAGE_DATA["contacts"]
    assert str(event.result_object_id) == response.json()["result_object_id"]


async def test_approve_free_event_without_price(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    free = {key: value for key, value in EVENT_DATA.items() if key != "price"} | {"is_free": True}
    [item] = await saved(session, Application(type="event", author_user_id=author.id, payload=free))

    response = await decide(client, moderator, item.id, {"decision": "approve"})
    assert response.status_code == 200, response.text
    [event] = await outbox_events(session, item.id)
    assert isinstance(event, ApplicationApproved)
    assert event.event is not None
    assert (event.event.is_free, event.event.price) == (True, None)


async def test_approve_keeps_optional_comment(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    [item] = await saved(session, application(author.id))
    response = await decide(
        client, moderator, item.id, {"decision": "approve", "comment": "  Всё в порядке  "}
    )
    assert response.json()["moderator_comment"] == "Всё в порядке"
    [entry] = await log_entries(session, item.id)
    assert entry.comment == "Всё в порядке"


# Отклонение (#18106, шаги 4.10-4.12, п. 5.1)


async def test_reject_with_comment(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    [item] = await saved(session, application(author.id))
    comment = "Укажите точное место проведения и контакты организатора."

    response = await decide(client, moderator, item.id, {"decision": "reject", "comment": comment})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["moderator_comment"]) == ("rejected", comment)
    assert body["result_object_id"] is None
    assert body["processed_by"] == str(moderator.id)

    [event] = await outbox_events(session, item.id)
    assert isinstance(event, ApplicationRejected)
    assert (event.author_user_id, event.moderator_comment, event.processed_by) == (
        author.id,
        comment,
        moderator.id,
    )

    [entry] = await log_entries(session, item.id)
    assert (entry.decision, entry.comment) == ("rejected", comment)

    # автор видит результат проверки и комментарий в «моих заявках» (п. 4.12, 7.4)
    mine = await client.get(f"/v1/me/applications/{item.id}", headers=author.headers)
    assert (mine.json()["status"], mine.json()["moderator_comment"]) == ("rejected", comment)


@pytest.mark.parametrize("body", [{"decision": "reject"}, {"decision": "reject", "comment": "   "}])
async def test_reject_requires_comment(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    moderator: User,
    author: User,
    body: dict[str, Any],
) -> None:
    [item] = await saved(session, application(author.id))

    response = await decide(client, moderator, item.id, body)
    assert_validation_error(
        response,
        COMMENT_REQUIRED,
        [{"field": "comment", "message": "Комментарий обязателен при отклонении заявки."}],
    )
    await session.refresh(item)
    assert item.status == "on_review"
    assert await outbox_events(session, item.id) == []
    assert await log_entries(session, item.id) == []


@pytest.mark.parametrize(
    ("body", "message", "field"),
    [
        ({}, "Заполните обязательные поля.", "decision"),
        ({"decision": "postpone"}, "Выберите решение: approve или reject.", "decision"),
        ({"decision": "reject", "comment": 42}, "Проверьте введённые данные.", "comment"),
        ({"decision": "reject", "comment": "а" * 1001}, "Не больше 1000 символов.", "comment"),
        ([], "Проверьте введённые данные.", "body"),
    ],
)
async def test_invalid_decision(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    moderator: User,
    author: User,
    body: Any,
    message: str,
    field: str,
) -> None:
    [item] = await saved(session, application(author.id))
    response = await decide(client, moderator, item.id, body)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["message"] == message
    assert [detail["field"] for detail in error["details"]] == [field]


async def test_decision_on_missing_application(client: httpx2.AsyncClient, moderator: User) -> None:
    response = await decide(client, moderator, uuid.uuid4(), {"decision": "approve"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "application_not_found"


# Заявка уже обработана (#18106, ошибка 6.1, п. 5.2)


@pytest.mark.parametrize(
    "second", [{"decision": "approve"}, {"decision": "reject", "comment": "Поздно"}]
)
async def test_decision_on_processed_application(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    make_user: Callable[..., User],
    author: User,
    second: dict[str, str],
) -> None:
    first_moderator, second_moderator = make_user(["moderator"]), make_user(["moderator"])
    [item] = await saved(session, application(author.id))
    first = await decide(client, first_moderator, item.id, {"decision": "approve"})
    assert first.status_code == 200

    response = await decide(client, second_moderator, item.id, second)
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "application_already_processed",
        "message": ALREADY_PROCESSED,
    }
    card = await client.get(
        f"/v1/moderation/applications/{item.id}", headers=second_moderator.headers
    )
    assert card.json()["status"] == "approved"
    assert card.json()["processed_by"] == str(first_moderator.id)
    assert len(await outbox_events(session, item.id)) == 1
    assert len(await log_entries(session, item.id)) == 1


async def test_after_approval_author_can_submit_same_application(
    client: httpx2.AsyncClient, session: AsyncSession, moderator: User, author: User
) -> None:
    """Дубликат проверяется только среди заявок на проверке (#18090, п. 5.3)."""
    [item] = await saved(session, application(author.id))
    await decide(client, moderator, item.id, {"decision": "approve"})
    await saved(session, application(author.id))


# Решения с настоящими транзакциями и RabbitMQ (критерий приёмки 2)


@dataclass
class Committed:
    """Заявки с настоящим коммитом: блокировки и outbox видны другим сессиям."""

    sessions: async_sessionmaker[AsyncSession]
    created: list[uuid.UUID]

    async def add(self, item: Application) -> uuid.UUID:
        async with self.sessions.begin() as session:
            session.add(item)
            await session.flush()
        self.created.append(item.id)
        return item.id


@pytest.fixture
async def committed(settings: Settings) -> AsyncIterator[Committed]:
    """После теста удаляет его заявки, записи журнала и события, чтобы не мешать другим тестам."""
    engine = create_engine(settings)
    data = Committed(create_sessionmaker(engine), [])
    try:
        yield data
    finally:
        async with data.sessions.begin() as session:
            ids = [str(application_id) for application_id in data.created]
            await session.execute(
                delete(OutboxMessage).where(
                    OutboxMessage.payload["payload"]["application_id"].astext.in_(ids)
                )
            )
            await session.execute(
                delete(ModerationLogEntry).where(ModerationLogEntry.object_id.in_(data.created))
            )
            await session.execute(delete(Application).where(Application.id.in_(data.created)))
        await engine.dispose()


async def test_concurrent_decisions_accept_only_first(committed: Committed, author: User) -> None:
    application_id = await committed.add(application(author.id))
    moderators = [uuid.uuid4(), uuid.uuid4()]

    async def attempt(moderator_id: uuid.UUID, decision: Approve | Reject) -> str:
        async with committed.sessions() as session:
            try:
                await service.decide(session, moderator_id, application_id, decision)
            except ApiError as exc:
                return exc.code
            return "ok"

    results = await asyncio.gather(
        attempt(moderators[0], Approve(comment=None)),
        attempt(moderators[1], Reject(comment="Нет адреса")),
    )
    assert sorted(results) == ["application_already_processed", "ok"]

    async with committed.sessions() as session:
        assert len(await log_entries(session, application_id)) == 1
        assert len(await outbox_events(session, application_id)) == 1


async def test_approved_event_is_delivered_to_broker(
    committed: Committed, rabbitmq_url: str, author: User
) -> None:
    sessions = committed.sessions
    application_id = await committed.add(application(author.id))
    moderator_id = uuid.uuid4()
    received: asyncio.Queue[EventEnvelope[BaseEvent]] = asyncio.Queue()

    async def consume(session: AsyncSession, envelope: EventEnvelope[BaseEvent]) -> None:
        await received.put(envelope)

    broker = RabbitBroker(rabbitmq_url)
    subscribe_event(
        broker,
        sessions,
        queue=f"test.moderation.{uuid.uuid4()}",
        routing_key=ApplicationApproved.routing_key,
        handler=consume,
    )
    await broker.start()
    try:
        async with sessions() as session:
            approved = await service.decide(
                session, moderator_id, application_id, Approve(comment=None)
            )
            result_object_id = approved.result_object_id

        while await publish_pending(sessions, broker):
            pass
        envelope = await asyncio.wait_for(received.get(), timeout=10)
    finally:
        await broker.stop()

    event = envelope.payload
    assert isinstance(event, ApplicationApproved)
    assert (event.application_id, event.result_object_id, event.processed_by) == (
        application_id,
        result_object_id,
        moderator_id,
    )
    async with sessions() as session:
        [row] = await session.scalars(
            select(OutboxMessage).where(OutboxMessage.id == envelope.event_id)
        )
        assert row.published_at is not None
        [entry] = await log_entries(session, application_id)
        assert entry.decision == "approved"

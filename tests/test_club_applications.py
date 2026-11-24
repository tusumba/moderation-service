"""Заявка на мероприятие от имени секции (#19427): требование #18102."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tusumba_common.testing import make_token
from tusumba_contracts import ApplicationApproved, ApplicationRejected, parse_envelope

from app.applications.validation import today_in_novosibirsk
from app.models import Application, OutboxMessage
from tests.conftest import FakeEvents

TODAY = date(2026, 12, 1)
EVENT = {
    "title": "Отчётный концерт музклуба",
    "description": "Выступления групп музклуба НГУ.",
    "date": "2026-12-20",
    "place_address": "Новосибирск, ул. Пирогова, 1, Большая актовая аудитория",
    "is_free": True,
}
NO_RIGHTS = "У вас нет прав на выбранную секцию."
NOT_SENT = "Не удалось отправить заявку. Попробуйте позже."


@pytest.fixture(autouse=True)
def fixed_today(app: FastAPI) -> None:
    app.dependency_overrides[today_in_novosibirsk] = lambda: TODAY


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
def admin(make_user: Callable[..., User]) -> User:
    return make_user()


@pytest.fixture
def moderator(make_user: Callable[..., User]) -> User:
    return make_user(["moderator"])


def from_club(club_id: uuid.UUID | str, **overrides: Any) -> dict[str, Any]:
    return {"type": "event", "club_id": str(club_id), "event": {**EVENT, **overrides}}


async def submit(client: httpx2.AsyncClient, user: User, body: Any) -> httpx2.Response:
    return await client.post("/v1/applications", json=body, headers=user.headers)


async def count_applications(session: AsyncSession, user: User) -> int:
    total = await session.scalar(
        select(func.count()).select_from(Application).where(Application.author_user_id == user.id)
    )
    return total or 0


async def outbox_payload(session: AsyncSession, application_id: str) -> dict[str, Any]:
    [row] = await session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.payload["payload"]["application_id"].astext == application_id
        )
    )
    return row.payload


def assert_error(response: httpx2.Response, status: int, code: str, message: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"] == {"code": code, "message": message}


# Основной сценарий (#18102, шаги 4.3-4.8)


async def test_club_admin_submits_application_from_club(
    client: httpx2.AsyncClient, session: AsyncSession, events: FakeEvents, admin: User
) -> None:
    club_id = events.add_club(admin.id)

    response = await submit(client, admin, from_club(club_id))

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["club_id"], body["author_user_id"], body["status"]) == (
        str(club_id),
        str(admin.id),
        "on_review",
    )
    [request] = events.requests
    assert request.url.path == f"/internal/clubs/{club_id}/permissions"
    assert request.url.params["user_id"] == str(admin.id)

    mine = await client.get(f"/v1/me/applications/{body['id']}", headers=admin.headers)
    assert mine.json()["club_id"] == str(club_id)


async def test_moderator_sees_club_in_queue_and_card(
    client: httpx2.AsyncClient, events: FakeEvents, admin: User, moderator: User
) -> None:
    club_id = events.add_club(admin.id)
    application_id = (await submit(client, admin, from_club(club_id))).json()["id"]

    queue = await client.get("/v1/moderation/applications", headers=moderator.headers)
    [item] = [item for item in queue.json()["items"] if item["id"] == application_id]
    assert item["club_id"] == str(club_id)
    card = await client.get(
        f"/v1/moderation/applications/{application_id}", headers=moderator.headers
    )
    assert card.json()["club_id"] == str(club_id)


async def test_one_of_several_admins_can_submit(
    client: httpx2.AsyncClient, events: FakeEvents, admin: User, make_user: Callable[..., User]
) -> None:
    other_admin = make_user()
    club_id = events.add_club(admin.id, other_admin.id)
    response = await submit(client, other_admin, from_club(club_id))
    assert response.status_code == 201, response.text


# После решения модератора (#18102, п. 4.9, 5.1; критерий приёмки задачи)


async def test_approved_event_is_published_with_club_as_organizer(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    events: FakeEvents,
    admin: User,
    moderator: User,
) -> None:
    club_id = events.add_club(admin.id)
    application_id = (await submit(client, admin, from_club(club_id))).json()["id"]

    response = await client.post(
        f"/v1/moderation/applications/{application_id}/decision",
        json={"decision": "approve"},
        headers=moderator.headers,
    )
    assert response.status_code == 200, response.text

    event = parse_envelope(await outbox_payload(session, application_id)).payload
    assert isinstance(event, ApplicationApproved)
    # по club_id events-service ставит секцию организатором мероприятия
    assert (event.club_id, event.author_user_id) == (club_id, admin.id)
    assert event.event is not None and event.event.title == EVENT["title"]


async def test_rejected_application_from_club_is_not_published(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    events: FakeEvents,
    admin: User,
    moderator: User,
) -> None:
    club_id = events.add_club(admin.id)
    application_id = (await submit(client, admin, from_club(club_id))).json()["id"]
    comment = "Уточните время начала концерта."

    await client.post(
        f"/v1/moderation/applications/{application_id}/decision",
        json={"decision": "reject", "comment": comment},
        headers=moderator.headers,
    )

    # ApplicationApproved нет, поэтому events-service мероприятие не создаёт
    event = parse_envelope(await outbox_payload(session, application_id)).payload
    assert isinstance(event, ApplicationRejected)
    assert (event.club_id, event.moderator_comment) == (club_id, comment)
    mine = await client.get(f"/v1/me/applications/{application_id}", headers=admin.headers)
    assert (mine.json()["status"], mine.json()["moderator_comment"]) == ("rejected", comment)


# Права на секцию (#18102, ошибка 6.1)


async def test_not_admin_cannot_submit_from_club(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    events: FakeEvents,
    admin: User,
    make_user: Callable[..., User],
) -> None:
    stranger = make_user()
    club_id = events.add_club(admin.id)

    response = await submit(client, stranger, from_club(club_id))

    assert_error(response, 403, "club_forbidden", NO_RIGHTS)
    assert await count_applications(session, stranger) == 0


async def test_admin_of_other_club_cannot_submit(
    client: httpx2.AsyncClient, session: AsyncSession, events: FakeEvents, admin: User
) -> None:
    events.add_club(admin.id)
    other_club = events.add_club(uuid.uuid4())

    response = await submit(client, admin, from_club(other_club))

    assert_error(response, 403, "club_forbidden", NO_RIGHTS)
    assert await count_applications(session, admin) == 0


async def test_unknown_or_blocked_club(
    client: httpx2.AsyncClient, session: AsyncSession, admin: User
) -> None:
    response = await submit(client, admin, from_club(uuid.uuid4()))

    assert_error(response, 404, "club_not_found", "Секция недоступна.")
    assert await count_applications(session, admin) == 0


# events-service не ответил (#18102, ошибка 6.2)


@pytest.mark.parametrize(
    "failure", [httpx.ConnectError("events недоступен"), httpx.ReadTimeout("таймаут"), 500, 403]
)
async def test_events_unavailable(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    events: FakeEvents,
    admin: User,
    failure: Exception | int,
) -> None:
    club_id = events.add_club(admin.id)
    events.failure = failure

    response = await submit(client, admin, from_club(club_id))

    assert_error(response, 503, "service_unavailable", NOT_SENT)
    assert await count_applications(session, admin) == 0


# Проверка тела запроса


@pytest.mark.parametrize("club_id", ["not-a-uuid", 42, ""])
async def test_club_id_must_be_uuid(
    client: httpx2.AsyncClient, events: FakeEvents, admin: User, club_id: Any
) -> None:
    response = await submit(client, admin, {**from_club(uuid.uuid4()), "club_id": club_id})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == [
        {"field": "club_id", "message": "Некорректное значение."}
    ]
    assert events.requests == []


async def test_club_page_application_cannot_be_from_club(
    client: httpx2.AsyncClient, events: FakeEvents, admin: User
) -> None:
    club_id = events.add_club(admin.id)
    body = {
        "type": "club_page",
        "club_id": str(club_id),
        "club_page": {"name": "Музклуб", "description": "Играем вместе."},
    }
    response = await submit(client, admin, body)
    message = "От имени секции подаётся только заявка на мероприятие."
    assert response.status_code == 422
    assert response.json()["error"]["message"] == message
    assert response.json()["error"]["details"] == [{"field": "club_id", "message": message}]
    assert events.requests == []


async def test_fields_are_checked_before_club_rights(
    client: httpx2.AsyncClient, events: FakeEvents, admin: User
) -> None:
    """Сначала обязательные поля (#18102, п. 4.6), events-service не вызывается."""
    club_id = events.add_club()
    response = await submit(client, admin, from_club(club_id, title=""))
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Заполните обязательные поля."
    assert events.requests == []


async def test_application_without_club_does_not_ask_events(
    client: httpx2.AsyncClient, events: FakeEvents, admin: User
) -> None:
    response = await submit(client, admin, {"type": "event", "event": EVENT})
    assert response.status_code == 201, response.text
    assert response.json()["club_id"] is None
    assert events.requests == []

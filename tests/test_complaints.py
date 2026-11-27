"""Подача жалобы с фиксацией версии текста (#19429): сценарии из требования #18120."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import httpx2
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tusumba_common.testing import make_token

from app.models import COMPLAINT_DUPLICATE_INDEX, Complaint
from tests.conftest import FakeEvents

REASON_REQUIRED = "Выберите причину жалобы."
DUPLICATE = "Жалоба уже отправлена."
NOT_SENT = "Не удалось отправить жалобу. Попробуйте позже."
UNAVAILABLE = "Объект жалобы недоступен."


@dataclass(frozen=True)
class User:
    id: uuid.UUID
    headers: dict[str, str]


@pytest.fixture
def make_user(keypair: tuple[str, str]) -> Callable[[], User]:
    def make() -> User:
        user_id = uuid.uuid4()
        token = make_token(keypair[0], user_id=str(user_id))
        return User(user_id, {"Authorization": f"Bearer {token}"})

    return make


@pytest.fixture
def ann(make_user: Callable[[], User]) -> User:
    return make_user()


def complaint(target_type: str, target_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    return {
        "target_type": target_type,
        "target_id": str(target_id),
        "reason": "misleading_info",
        "comment": "В описании указана бесплатная запись, а на входе просят деньги.",
        **overrides,
    }


async def submit(client: httpx2.AsyncClient, user: User, body: Any) -> httpx2.Response:
    return await client.post("/v1/complaints", json=body, headers=user.headers)


async def count_complaints(session: AsyncSession, user: User) -> int:
    total = await session.scalar(
        select(func.count()).select_from(Complaint).where(Complaint.author_user_id == user.id)
    )
    return total or 0


def assert_error(response: httpx2.Response, status: int, code: str, message: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"] == {"code": code, "message": message}


# Основной сценарий (#18120, шаги 4.4-4.9)


@pytest.mark.parametrize(("target_type", "kind"), [("event", "events"), ("club", "clubs")])
async def test_complaint_is_saved_with_current_version(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    events: FakeEvents,
    ann: User,
    target_type: str,
    kind: str,
) -> None:
    target_id, version_id = events.add_target(kind)

    response = await submit(client, ann, complaint(target_type, target_id))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body == {
        "id": body["id"],
        "target_type": target_type,
        "target_id": str(target_id),
        "target_version_id": str(version_id),
        "reason": "misleading_info",
        "comment": "В описании указана бесплатная запись, а на входе просят деньги.",
        "status": "new",
        "moderator_comment": None,
        "created_at": body["created_at"],
        "processed_at": None,
    }
    [request] = events.requests
    assert request.url.path == f"/internal/{kind}/{target_id}"

    saved = await session.get_one(Complaint, uuid.UUID(body["id"]))
    assert (saved.author_user_id, saved.status, saved.target_version_id) == (
        ann.id,
        "new",
        version_id,
    )


async def test_edit_after_complaint_does_not_change_saved_version(
    client: httpx2.AsyncClient, session: AsyncSession, events: FakeEvents, ann: User
) -> None:
    """Модератор увидит текст, на который пожаловались, а не отредактированный."""
    target_id, version_id = events.add_target("events")
    complaint_id = (await submit(client, ann, complaint("event", target_id))).json()["id"]

    events.new_version("events", target_id)

    saved = await session.get_one(Complaint, uuid.UUID(complaint_id))
    assert saved.target_version_id == version_id


async def test_comment_is_optional(
    client: httpx2.AsyncClient, events: FakeEvents, ann: User
) -> None:
    target_id, _ = events.add_target("events")
    body = complaint("event", target_id, reason="spam")
    del body["comment"]

    response = await submit(client, ann, body)

    assert response.status_code == 201, response.text
    assert response.json()["comment"] is None


async def test_blank_comment_is_not_saved(
    client: httpx2.AsyncClient, events: FakeEvents, ann: User
) -> None:
    target_id, _ = events.add_target("events")
    response = await submit(client, ann, complaint("event", target_id, comment="   "))
    assert response.json()["comment"] is None


@pytest.mark.parametrize(
    "reason", ["fraud", "misleading_info", "offensive", "prohibited_content", "spam", "other"]
)
async def test_every_reason_is_accepted(
    client: httpx2.AsyncClient, events: FakeEvents, ann: User, reason: str
) -> None:
    target_id, _ = events.add_target("clubs")
    response = await submit(client, ann, complaint("club", target_id, reason=reason))
    assert response.status_code == 201, response.text


async def test_requires_login(client: httpx2.AsyncClient, events: FakeEvents) -> None:
    target_id, _ = events.add_target("events")
    response = await client.post("/v1/complaints", json=complaint("event", target_id))
    assert response.status_code == 401
    assert events.requests == []


# Повторная жалоба (#18120, п. 5.2, ошибка 6.2)


async def test_repeated_complaint_while_not_reviewed(
    client: httpx2.AsyncClient, session: AsyncSession, events: FakeEvents, ann: User
) -> None:
    target_id, _ = events.add_target("events")
    first = await submit(client, ann, complaint("event", target_id))
    assert first.status_code == 201

    response = await submit(client, ann, complaint("event", target_id, reason="fraud"))

    assert_error(response, 409, "complaint_duplicate", DUPLICATE)
    assert await count_complaints(session, ann) == 1


async def test_repeated_complaint_on_extra_check(
    client: httpx2.AsyncClient, session: AsyncSession, events: FakeEvents, ann: User
) -> None:
    target_id, _ = events.add_target("events")
    complaint_id = (await submit(client, ann, complaint("event", target_id))).json()["id"]
    await session.execute(
        update(Complaint)
        .where(Complaint.id == uuid.UUID(complaint_id))
        .values(status="extra_check")
    )

    response = await submit(client, ann, complaint("event", target_id))

    assert_error(response, 409, "complaint_duplicate", DUPLICATE)


@pytest.mark.parametrize("decided", ["true", "false"])
async def test_new_complaint_after_decision(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    events: FakeEvents,
    ann: User,
    decided: str,
) -> None:
    target_id, _ = events.add_target("events")
    complaint_id = (await submit(client, ann, complaint("event", target_id))).json()["id"]
    await session.execute(
        update(Complaint).where(Complaint.id == uuid.UUID(complaint_id)).values(status=decided)
    )

    response = await submit(client, ann, complaint("event", target_id))

    assert response.status_code == 201, response.text
    assert await count_complaints(session, ann) == 2


async def test_other_users_complain_about_same_object(
    client: httpx2.AsyncClient, events: FakeEvents, ann: User, make_user: Callable[[], User]
) -> None:
    target_id, _ = events.add_target("events")
    assert (await submit(client, ann, complaint("event", target_id))).status_code == 201
    assert (await submit(client, make_user(), complaint("event", target_id))).status_code == 201


async def test_same_user_complains_about_event_and_club(
    client: httpx2.AsyncClient, events: FakeEvents, ann: User
) -> None:
    event_id, _ = events.add_target("events")
    club_id, _ = events.add_target("clubs")
    assert (await submit(client, ann, complaint("event", event_id))).status_code == 201
    assert (await submit(client, ann, complaint("club", club_id))).status_code == 201


async def test_duplicate_index_protects_concurrent_complaints(
    session: AsyncSession, ann: User
) -> None:
    """Две одновременные отправки: вторую не пропустит частичный уникальный индекс."""
    target_id = uuid.uuid4()

    def row() -> Complaint:
        return Complaint(
            target_type="event",
            target_id=target_id,
            target_version_id=uuid.uuid4(),
            author_user_id=ann.id,
            reason="spam",
        )

    session.add(row())
    await session.flush()
    session.add(row())
    with pytest.raises(IntegrityError, match=COMPLAINT_DUPLICATE_INDEX):
        await session.flush()
    await session.rollback()


# Объект жалобы недоступен (#18120, ошибка 6.4)


@pytest.mark.parametrize(
    ("target_type", "kind", "status"),
    [("event", "events", None), ("club", "clubs", None), ("club", "clubs", "blocked")],
)
async def test_unavailable_target(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    events: FakeEvents,
    ann: User,
    target_type: str,
    kind: str,
    status: str | None,
) -> None:
    # объекта нет (удалённое мероприятие events-service отдаёт как 404) или секция заблокирована
    target_id = events.add_target(kind, status)[0] if status else uuid.uuid4()

    response = await submit(client, ann, complaint(target_type, target_id))

    assert_error(response, 404, "complaint_target_unavailable", UNAVAILABLE)
    assert await count_complaints(session, ann) == 0


# events-service не ответил (#18120, ошибка 6.3)


@pytest.mark.parametrize(
    "failure", [httpx.ConnectError("events недоступен"), httpx.ReadTimeout("таймаут"), 500, 403]
)
async def test_events_unavailable(
    client: httpx2.AsyncClient,
    session: AsyncSession,
    events: FakeEvents,
    ann: User,
    failure: Exception | int,
) -> None:
    target_id, _ = events.add_target("events")
    events.failure = failure

    response = await submit(client, ann, complaint("event", target_id))

    assert_error(response, 503, "service_unavailable", NOT_SENT)
    assert await count_complaints(session, ann) == 0


async def test_unexpected_events_response(
    client: httpx2.AsyncClient, events: FakeEvents, ann: User
) -> None:
    target_id, _ = events.add_target("events")
    events.targets["events", target_id] = {"id": str(target_id)}
    response = await submit(client, ann, complaint("event", target_id))
    assert_error(response, 503, "service_unavailable", NOT_SENT)


# Проверка тела (#18120, ошибка 6.1)


@pytest.mark.parametrize("reason", [None, "", "boring"])
async def test_reason_is_required(
    client: httpx2.AsyncClient, events: FakeEvents, ann: User, reason: str | None
) -> None:
    body = complaint("event", uuid.uuid4(), reason=reason)
    if reason is None:
        del body["reason"]

    response = await submit(client, ann, body)

    assert response.status_code == 422, response.text
    assert response.json()["error"] == {
        "code": "validation_error",
        "message": REASON_REQUIRED,
        "details": [{"field": "reason", "message": REASON_REQUIRED}],
    }
    assert events.requests == []


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"target_type": "user"}, "target_type"),
        ({"target_type": None}, "target_type"),
        ({"target_id": "not-a-uuid"}, "target_id"),
        ({"target_id": 42}, "target_id"),
        ({"target_id": None}, "target_id"),
        ({"comment": 42}, "comment"),
        ({"comment": "а" * 1001}, "comment"),
    ],
)
async def test_invalid_fields(
    client: httpx2.AsyncClient,
    events: FakeEvents,
    ann: User,
    overrides: dict[str, Any],
    field: str,
) -> None:
    response = await submit(client, ann, {**complaint("event", uuid.uuid4()), **overrides})
    assert response.status_code == 422, response.text
    assert [detail["field"] for detail in response.json()["error"]["details"]] == [field]
    assert events.requests == []


async def test_all_field_errors_at_once(client: httpx2.AsyncClient, ann: User) -> None:
    response = await submit(client, ann, {"comment": "а" * 1001})
    error = response.json()["error"]
    assert error["message"] == REASON_REQUIRED
    assert [detail["field"] for detail in error["details"]] == [
        "target_type",
        "target_id",
        "reason",
        "comment",
    ]


@pytest.mark.parametrize("body", [[], "жалоба", None])
async def test_body_must_be_object(client: httpx2.AsyncClient, ann: User, body: Any) -> None:
    response = await submit(client, ann, body)
    assert response.status_code == 422

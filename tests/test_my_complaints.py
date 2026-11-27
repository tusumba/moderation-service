"""Статус своих жалоб (#19430): сценарии из требования #18121."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tusumba_common.testing import make_token

from app.models import Complaint

NOT_FOUND = {"code": "complaint_not_found", "message": "Жалоба не найдена."}


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


@pytest.fixture
def bob(make_user: Callable[[], User]) -> User:
    return make_user()


def complaint(author: User, **fields: Any) -> Complaint:
    return Complaint(
        target_type=fields.pop("target_type", "event"),
        target_id=fields.pop("target_id", uuid.uuid4()),
        target_version_id=uuid.uuid4(),
        author_user_id=author.id,
        reason=fields.pop("reason", "misleading_info"),
        **fields,
    )


async def saved(session: AsyncSession, *complaints: Complaint) -> list[Complaint]:
    session.add_all(complaints)
    await session.flush()
    return list(complaints)


async def my_complaints(client: httpx2.AsyncClient, user: User, **params: Any) -> httpx2.Response:
    return await client.get("/v1/me/complaints", params=params, headers=user.headers)


# Список (#18121, шаги 4.1-4.2, п. 5.1)


async def test_list_shows_only_own_newest_first(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User, bob: User
) -> None:
    now = datetime.now(UTC)
    old, new = await saved(
        session,
        complaint(ann, created_at=now - timedelta(days=2)),
        complaint(ann, target_type="club", reason="spam", created_at=now - timedelta(days=1)),
    )
    await saved(session, complaint(bob))

    response = await my_complaints(client, ann)

    assert response.status_code == 200, response.text
    page = response.json()
    assert [item["id"] for item in page["items"]] == [str(new.id), str(old.id)]
    assert (page["total"], page["page"], page["size"]) == (2, 1, 20)


async def test_empty_list(client: httpx2.AsyncClient, ann: User) -> None:
    response = await my_complaints(client, ann)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "size": 20}


async def test_list_is_paginated(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User
) -> None:
    now = datetime.now(UTC)
    items = await saved(
        session, *(complaint(ann, created_at=now + timedelta(minutes=i)) for i in range(3))
    )
    page = (await my_complaints(client, ann, page=2, size=2)).json()
    assert [item["id"] for item in page["items"]] == [str(items[0].id)]
    assert (page["total"], page["page"], page["size"]) == (3, 2, 2)


@pytest.mark.parametrize("params", [{"page": 0}, {"size": 0}, {"size": 101}])
async def test_invalid_pagination(
    client: httpx2.AsyncClient, ann: User, params: dict[str, int]
) -> None:
    response = await my_complaints(client, ann, **params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_list_requires_login(client: httpx2.AsyncClient) -> None:
    response = await client.get("/v1/me/complaints")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


# Жалоба (#18121, шаги 4.3-4.8)


async def test_new_complaint_shows_reason_date_and_status(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User
) -> None:
    [item] = await saved(session, complaint(ann, text="Просят деньги на входе."))

    response = await client.get(f"/v1/me/complaints/{item.id}", headers=ann.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "id": str(item.id),
        "target_type": "event",
        "target_id": str(item.target_id),
        "target_version_id": str(item.target_version_id),
        "reason": "misleading_info",
        "comment": "Просят деньги на входе.",
        "status": "new",
        "moderator_comment": None,
        "created_at": body["created_at"],
        "processed_at": None,
    }
    assert datetime.fromisoformat(body["created_at"]) == item.created_at


@pytest.mark.parametrize("status", ["true", "false", "extra_check"])
async def test_processed_complaint_shows_result(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User, status: str
) -> None:
    """После решения видны статус, комментарий модератора и время обработки (п. 4.8)."""
    processed_at = datetime.now(UTC)
    [item] = await saved(
        session,
        complaint(
            ann,
            status=status,
            moderator_comment="Мероприятие проверено.",
            processed_by=uuid.uuid4(),
            processed_at=processed_at,
        ),
    )

    body = (await client.get(f"/v1/me/complaints/{item.id}", headers=ann.headers)).json()

    assert (body["status"], body["moderator_comment"]) == (status, "Мероприятие проверено.")
    assert datetime.fromisoformat(body["processed_at"]) == processed_at
    assert "processed_by" not in body


async def test_list_items_match_card(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User
) -> None:
    [item] = await saved(session, complaint(ann))
    [listed] = (await my_complaints(client, ann)).json()["items"]
    card = (await client.get(f"/v1/me/complaints/{item.id}", headers=ann.headers)).json()
    assert listed == card


# Жалоба не найдена (#18121, ошибка 6.1)


async def test_other_users_complaint_is_not_found(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User, bob: User
) -> None:
    [item] = await saved(session, complaint(bob))
    response = await client.get(f"/v1/me/complaints/{item.id}", headers=ann.headers)
    assert response.status_code == 404
    assert response.json()["error"] == NOT_FOUND


async def test_missing_complaint_is_not_found(client: httpx2.AsyncClient, ann: User) -> None:
    response = await client.get(f"/v1/me/complaints/{uuid.uuid4()}", headers=ann.headers)
    assert response.status_code == 404
    assert response.json()["error"] == NOT_FOUND


async def test_card_requires_login(client: httpx2.AsyncClient) -> None:
    response = await client.get(f"/v1/me/complaints/{uuid.uuid4()}")
    assert response.status_code == 401

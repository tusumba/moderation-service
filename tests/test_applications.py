"""Подача заявки, проверка дублей и «мои заявки» (#19049): сценарии из требований #18090, #18098."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tusumba_common.testing import make_token

from app.models import DUPLICATE_INDEX, Application

TODAY = datetime.now(UTC).date()
NEXT_MONTH = (TODAY + timedelta(days=30)).isoformat()
REQUIRED_FIELDS = "Заполните обязательные поля."
DUPLICATE = "У вас уже есть активная заявка на этот объект."


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


def event(**overrides: Any) -> dict[str, Any]:
    data = {
        "title": "Посвящение в студенты ФИТ",
        "description": "Концерт, конкурсы и дискотека для первокурсников.",
        "date": NEXT_MONTH,
        "place_address": "Новосибирск, ул. Пирогова, 1, Большая актовая аудитория",
        "is_free": True,
    }
    return {"type": "event", "event": {**data, **overrides}}


def club_page(**overrides: Any) -> dict[str, Any]:
    data = {
        "name": "Клуб настольных игр",
        "description": "Играем в настольные игры каждую пятницу.",
        "meeting_place": "Новосибирск, ул. Пирогова, 1, ауд. 3107",
        "schedule": "Пятница, 18:00-21:00",
        "contacts": {"telegram": "@nsu_boardgames"},
    }
    return {"type": "club_page", "club_page": {**data, **overrides}}


async def submit(client: httpx2.AsyncClient, user: User, body: Any) -> httpx2.Response:
    return await client.post("/v1/applications", json=body, headers=user.headers)


async def submitted(client: httpx2.AsyncClient, user: User, body: Any) -> dict[str, Any]:
    response = await submit(client, user, body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def assert_validation_error(
    response: httpx2.Response, message: str, details: list[dict[str, str]]
) -> None:
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == message
    assert error["details"] == details


async def count_applications(session: AsyncSession, user: User) -> int:
    total = await session.scalar(
        select(func.count()).select_from(Application).where(Application.author_user_id == user.id)
    )
    return total or 0


async def reject(session: AsyncSession, application_id: str, comment: str) -> None:
    await session.execute(
        update(Application)
        .where(Application.id == uuid.UUID(application_id))
        .values(
            status="rejected",
            moderator_comment=comment,
            processed_by=uuid.uuid4(),
            processed_at=func.now(),
        )
    )


# --- подача заявки ---


async def test_event_application_is_saved_on_review(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User
) -> None:
    body = await submitted(client, ann, event())

    assert body["type"] == "event"
    assert body["status"] == "on_review"
    assert body["author_user_id"] == str(ann.id)
    assert body["event"] == event()["event"]
    assert "club_page" not in body
    for field in ("club_id", "moderator_comment", "result_object_id", "processed_by"):
        assert body[field] is None
    assert body["processed_at"] is None
    assert body["created_at"]

    stored = await session.get(Application, uuid.UUID(body["id"]))
    assert stored is not None
    assert stored.author_user_id == ann.id
    assert stored.payload == event()["event"]


async def test_paid_event_keeps_price_and_free_event_drops_it(
    client: httpx2.AsyncClient, ann: User
) -> None:
    price = {"amount": 30000, "currency": "RUB"}
    paid = await submitted(client, ann, event(title="Концерт", is_free=False, price=price))
    assert paid["event"]["price"] == price

    free = await submitted(client, ann, event(title="Лекция", price=price))
    assert "price" not in free["event"]


async def test_club_page_application_is_saved(client: httpx2.AsyncClient, ann: User) -> None:
    body = await submitted(client, ann, club_page())

    assert body["type"] == "club_page"
    assert body["status"] == "on_review"
    assert body["club_page"] == club_page()["club_page"]
    assert "event" not in body


async def test_club_page_optional_fields_can_be_omitted(
    client: httpx2.AsyncClient, ann: User
) -> None:
    body = {"type": "club_page", "club_page": {"name": "Шахматы", "description": "Играем."}}
    created = await submitted(client, ann, body)
    assert created["club_page"] == {"name": "Шахматы", "description": "Играем."}


async def test_text_fields_are_trimmed(client: httpx2.AsyncClient, ann: User) -> None:
    body = await submitted(client, ann, event(title="  Посвящение  "))
    assert body["event"]["title"] == "Посвящение"


async def test_author_and_status_are_not_taken_from_body(
    client: httpx2.AsyncClient, ann: User
) -> None:
    body = {**event(), "author_user_id": str(uuid.uuid4()), "status": "approved"}
    created = await submitted(client, ann, body)
    assert created["author_user_id"] == str(ann.id)
    assert created["status"] == "on_review"


async def test_applications_require_authentication(client: httpx2.AsyncClient) -> None:
    for response in (
        await client.post("/v1/applications", json=event()),
        await client.get("/v1/me/applications"),
        await client.get(f"/v1/me/applications/{uuid.uuid4()}"),
    ):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"


async def test_token_subject_must_be_user_id(
    client: httpx2.AsyncClient, keypair: tuple[str, str]
) -> None:
    headers = {"Authorization": f"Bearer {make_token(keypair[0], user_id='1')}"}
    response = await client.post("/v1/applications", json=event(), headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


# --- проверка полей ---


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("title", "Заполните обязательное поле."),
        ("description", "Заполните обязательное поле."),
        ("date", "Заполните обязательное поле."),
        ("place_address", "Укажите место проведения."),
        ("is_free", "Заполните обязательное поле."),
    ],
)
async def test_event_requires_fields(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User, field: str, message: str
) -> None:
    body = event()
    del body["event"][field]
    response = await submit(client, ann, body)
    assert_validation_error(
        response, REQUIRED_FIELDS, [{"field": f"event.{field}", "message": message}]
    )
    assert await count_applications(session, ann) == 0


async def test_blank_text_counts_as_missing(client: httpx2.AsyncClient, ann: User) -> None:
    response = await submit(client, ann, event(title="   "))
    assert_validation_error(
        response,
        REQUIRED_FIELDS,
        [{"field": "event.title", "message": "Заполните обязательное поле."}],
    )


async def test_all_missing_fields_are_reported_at_once(
    client: httpx2.AsyncClient, ann: User
) -> None:
    response = await submit(client, ann, {"type": "event", "event": {}})
    assert response.status_code == 422
    fields = [detail["field"] for detail in response.json()["error"]["details"]]
    assert fields == [
        "event.title",
        "event.description",
        "event.date",
        "event.place_address",
        "event.is_free",
    ]


async def test_event_date_cannot_be_in_past(client: httpx2.AsyncClient, ann: User) -> None:
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    response = await submit(client, ann, event(date=yesterday))
    message = "Дата мероприятия не может быть в прошлом."
    assert_validation_error(response, message, [{"field": "event.date", "message": message}])


async def test_event_can_be_today(client: httpx2.AsyncClient, ann: User) -> None:
    await submitted(client, ann, event(date=TODAY.isoformat()))


@pytest.mark.parametrize("value", ["05.12.2026", "2026-02-30", "20261205", 20261205])
async def test_event_date_format(client: httpx2.AsyncClient, ann: User, value: Any) -> None:
    response = await submit(client, ann, event(date=value))
    message = "Укажите дату в формате ГГГГ-ММ-ДД."
    assert_validation_error(response, message, [{"field": "event.date", "message": message}])


async def test_paid_event_requires_price(client: httpx2.AsyncClient, ann: User) -> None:
    response = await submit(client, ann, event(is_free=False))
    assert_validation_error(
        response,
        REQUIRED_FIELDS,
        [{"field": "event.price", "message": "Укажите цену платного мероприятия."}],
    )


@pytest.mark.parametrize(
    ("price", "field"),
    [
        ({"amount": -1, "currency": "RUB"}, "event.price.amount"),
        ({"amount": "300", "currency": "RUB"}, "event.price.amount"),
        ({"amount": 10.5, "currency": "RUB"}, "event.price.amount"),
        ({"amount": True, "currency": "RUB"}, "event.price.amount"),
        ({"amount": 30000, "currency": "USD"}, "event.price.currency"),
    ],
)
async def test_price_must_be_valid(
    client: httpx2.AsyncClient, ann: User, price: dict[str, Any], field: str
) -> None:
    response = await submit(client, ann, event(is_free=False, price=price))
    assert response.status_code == 422
    assert [detail["field"] for detail in response.json()["error"]["details"]] == [field]


async def test_is_free_must_be_boolean(client: httpx2.AsyncClient, ann: User) -> None:
    response = await submit(client, ann, event(is_free="yes"))
    assert_validation_error(
        response,
        "Некорректное значение.",
        [{"field": "event.is_free", "message": "Некорректное значение."}],
    )


async def test_title_length_is_limited(client: httpx2.AsyncClient, ann: User) -> None:
    response = await submit(client, ann, event(title="а" * 256))
    message = "Не больше 255 символов."
    assert_validation_error(response, message, [{"field": "event.title", "message": message}])
    await submitted(client, ann, event(title="а" * 255))


async def test_type_is_required(client: httpx2.AsyncClient, ann: User) -> None:
    response = await submit(client, ann, {"event": event()["event"]})
    assert_validation_error(
        response, REQUIRED_FIELDS, [{"field": "type", "message": "Заполните обязательное поле."}]
    )


async def test_type_must_be_known(client: httpx2.AsyncClient, ann: User) -> None:
    response = await submit(client, ann, {**event(), "type": "club"})
    message = "Выберите тип заявки: event или club_page."
    assert_validation_error(response, message, [{"field": "type", "message": message}])


async def test_block_must_match_type(client: httpx2.AsyncClient, ann: User) -> None:
    response = await submit(client, ann, {**event(), "club_page": club_page()["club_page"]})
    message = "Блок не соответствует типу заявки."
    assert_validation_error(response, message, [{"field": "club_page", "message": message}])

    response = await submit(client, ann, {"type": "club_page", "event": event()["event"]})
    assert_validation_error(
        response,
        REQUIRED_FIELDS,
        [
            {"field": "event", "message": message},
            {"field": "club_page", "message": "Заполните обязательное поле."},
        ],
    )


async def test_club_page_requires_name_and_description(
    client: httpx2.AsyncClient, ann: User
) -> None:
    response = await submit(client, ann, {"type": "club_page", "club_page": {"name": " "}})
    assert_validation_error(
        response,
        REQUIRED_FIELDS,
        [
            {"field": "club_page.name", "message": "Заполните обязательное поле."},
            {"field": "club_page.description", "message": "Заполните обязательное поле."},
        ],
    )


async def test_club_page_contacts_must_be_strings(client: httpx2.AsyncClient, ann: User) -> None:
    response = await submit(client, ann, club_page(contacts={"telegram": 42}))
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "club_page.contacts"


@pytest.mark.parametrize("body", [[], "event", None])
async def test_body_must_be_object(client: httpx2.AsyncClient, ann: User, body: Any) -> None:
    response = await submit(client, ann, body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_application_on_behalf_of_club_is_not_available_yet(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User
) -> None:
    response = await submit(client, ann, {**event(), "club_id": str(uuid.uuid4())})
    message = "Заявка от имени секции пока недоступна."
    assert_validation_error(response, message, [{"field": "club_id", "message": message}])
    assert await count_applications(session, ann) == 0


# --- дубликаты (#18090, п. 5.3) ---


async def test_duplicate_application_is_rejected(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User
) -> None:
    await submitted(client, ann, event())

    response = await submit(client, ann, event())
    assert response.status_code == 409
    assert response.json()["error"] == {"code": "application_duplicate", "message": DUPLICATE}
    assert await count_applications(session, ann) == 1


async def test_duplicate_ignores_case_and_spaces(client: httpx2.AsyncClient, ann: User) -> None:
    await submitted(client, ann, club_page())
    response = await submit(client, ann, club_page(name="  КЛУБ настольных ИГР "))
    assert response.status_code == 409


@pytest.mark.parametrize(
    "second",
    [
        event(date=(TODAY + timedelta(days=31)).isoformat()),
        event(title="Посвящение в студенты ММФ"),
        club_page(name=event()["event"]["title"]),
    ],
    ids=["other-date", "other-title", "other-type"],
)
async def test_different_application_is_not_duplicate(
    client: httpx2.AsyncClient, ann: User, second: dict[str, Any]
) -> None:
    await submitted(client, ann, event())
    await submitted(client, ann, second)


async def test_same_application_of_another_author_is_not_duplicate(
    client: httpx2.AsyncClient, ann: User, bob: User
) -> None:
    await submitted(client, ann, event())
    await submitted(client, bob, event())


async def test_processed_application_does_not_block_new_one(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User
) -> None:
    first = await submitted(client, ann, event())
    await reject(session, first["id"], "Уточните место проведения.")

    second = await submitted(client, ann, event())
    assert second["id"] != first["id"]


async def test_database_rejects_second_application_on_review(
    session: AsyncSession, ann: User
) -> None:
    """Защита от двух одновременных отправок: дубликат не пропускает сама база."""
    payload = event()["event"]
    session.add(Application(type="event", author_user_id=ann.id, payload=payload))
    await session.flush()

    with pytest.raises(IntegrityError, match=DUPLICATE_INDEX):
        async with session.begin_nested():
            upper = {**payload, "title": payload["title"].upper()}
            session.add(Application(type="event", author_user_id=ann.id, payload=upper))


# --- мои заявки ---


async def test_my_applications_show_only_own_newest_first(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User, bob: User
) -> None:
    older = await submitted(client, ann, event())
    newer = await submitted(client, ann, club_page())
    await submitted(client, bob, event())
    # в тесте всё идёт в одной транзакции, поэтому now() у заявок одинаковый
    await session.execute(
        update(Application)
        .where(Application.id == uuid.UUID(older["id"]))
        .values(created_at=func.now() - timedelta(hours=1))
    )

    response = await client.get("/v1/me/applications", headers=ann.headers)
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [newer["id"], older["id"]]
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["size"] == 20
    assert body["items"][0]["club_page"] == club_page()["club_page"]


async def test_my_applications_filters(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User
) -> None:
    rejected = await submitted(client, ann, event())
    await reject(session, rejected["id"], "Нет описания программы.")
    on_review = await submitted(client, ann, event(title="Другое мероприятие"))
    club = await submitted(client, ann, club_page())

    async def ids(**params: str) -> list[str]:
        response = await client.get("/v1/me/applications", params=params, headers=ann.headers)
        assert response.status_code == 200
        return sorted(item["id"] for item in response.json()["items"])

    assert await ids(status="rejected") == [rejected["id"]]
    assert await ids(type="club_page") == [club["id"]]
    assert await ids(status="on_review", type="event") == [on_review["id"]]


async def test_my_applications_pagination(client: httpx2.AsyncClient, ann: User) -> None:
    for number in range(3):
        await submitted(client, ann, event(title=f"Мероприятие {number}"))

    response = await client.get(
        "/v1/me/applications", params={"page": 2, "size": 2}, headers=ann.headers
    )
    body = response.json()
    assert len(body["items"]) == 1
    assert (body["total"], body["page"], body["size"]) == (3, 2, 2)


async def test_my_applications_empty_list(client: httpx2.AsyncClient, ann: User) -> None:
    response = await client.get("/v1/me/applications", headers=ann.headers)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "size": 20}


@pytest.mark.parametrize(
    ("params", "field"),
    [({"size": 101}, "size"), ({"page": 0}, "page"), ({"status": "new"}, "status")],
)
async def test_my_applications_query_is_validated(
    client: httpx2.AsyncClient, ann: User, params: dict[str, Any], field: str
) -> None:
    response = await client.get("/v1/me/applications", params=params, headers=ann.headers)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["field"] == field


async def test_my_application_shows_moderator_comment(
    client: httpx2.AsyncClient, session: AsyncSession, ann: User
) -> None:
    created = await submitted(client, ann, event())
    await reject(session, created["id"], "Уточните место проведения.")

    response = await client.get(f"/v1/me/applications/{created['id']}", headers=ann.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["moderator_comment"] == "Уточните место проведения."
    assert body["processed_by"] is not None
    assert body["processed_at"] is not None
    assert body["event"] == event()["event"]


async def test_foreign_or_missing_application_is_not_found(
    client: httpx2.AsyncClient, ann: User, bob: User
) -> None:
    created = await submitted(client, ann, event())

    for path in (f"/v1/me/applications/{created['id']}", f"/v1/me/applications/{uuid.uuid4()}"):
        response = await client.get(path, headers=bob.headers)
        assert response.status_code == 404
        assert response.json()["error"] == {
            "code": "application_not_found",
            "message": "Заявка не найдена.",
        }

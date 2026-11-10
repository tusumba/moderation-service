"""POST /v1/applications, GET /v1/me/applications и /v1/me/applications/{id} по OpenAPI."""

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from app.api.deps import UserId
from app.applications import service
from app.applications.validation import parse_application
from app.db import Session
from app.models import Application, ApplicationStatus, ApplicationType

router = APIRouter(tags=["applications"])

# Тело разбираем сами (validation.py): в OpenAPI это ApplicationCreate.
RawBody = Annotated[Any, Body()]


def _optional() -> Any:
    """Необязательное поле: без значения в ответ не попадает, как в OpenAPI."""
    return Field(default=None, exclude_if=lambda value: value is None)


class Money(BaseModel):
    amount: int
    currency: Literal["RUB"]


class EventApplicationData(BaseModel):
    title: str
    description: str
    date: dt.date
    place_address: str
    is_free: bool
    price: Money | None = _optional()


class ClubPageApplicationData(BaseModel):
    name: str
    description: str
    meeting_place: str | None = _optional()
    schedule: str | None = _optional()
    contacts: dict[str, str] | None = _optional()


class ApplicationOut(BaseModel):
    """Заявка (схема Application в OpenAPI): данные в блоке event или club_page по типу."""

    id: uuid.UUID
    type: ApplicationType
    author_user_id: uuid.UUID
    club_id: uuid.UUID | None
    event: EventApplicationData | None = _optional()
    club_page: ClubPageApplicationData | None = _optional()
    status: ApplicationStatus
    moderator_comment: str | None
    result_object_id: uuid.UUID | None
    processed_by: uuid.UUID | None
    created_at: dt.datetime
    processed_at: dt.datetime | None

    @classmethod
    def of(cls, application: Application) -> "ApplicationOut":
        event, club_page = None, None
        if application.type == "event":
            event = EventApplicationData.model_validate(application.payload)
        else:
            club_page = ClubPageApplicationData.model_validate(application.payload)
        return cls(
            id=application.id,
            type=application.type,
            author_user_id=application.author_user_id,
            club_id=application.club_id,
            event=event,
            club_page=club_page,
            status=application.status,
            moderator_comment=application.moderator_comment,
            result_object_id=application.result_object_id,
            processed_by=application.processed_by,
            created_at=application.created_at,
            processed_at=application.processed_at,
        )


class ApplicationPage(BaseModel):
    items: list[ApplicationOut]
    total: int
    page: int
    size: int


@router.post("/applications", status_code=201)
async def create_application(user_id: UserId, db: Session, body: RawBody) -> ApplicationOut:
    new = parse_application(body, today=dt.datetime.now(dt.UTC).date())
    return ApplicationOut.of(await service.create(db, user_id, new))


@router.get("/me/applications")
async def list_my_applications(
    user_id: UserId,
    db: Session,
    status: ApplicationStatus | None = None,
    type_: Annotated[ApplicationType | None, Query(alias="type")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApplicationPage:
    rows, total = await service.list_mine(
        db, user_id, status=status, type_=type_, page=page, size=size
    )
    return ApplicationPage(
        items=[ApplicationOut.of(row) for row in rows], total=total, page=page, size=size
    )


@router.get("/me/applications/{id}")
async def get_my_application(user_id: UserId, db: Session, id: uuid.UUID) -> ApplicationOut:
    return ApplicationOut.of(await service.get_mine(db, user_id, id))

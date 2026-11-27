"""POST /v1/complaints (#19429), GET /v1/me/complaints и /v1/me/complaints/{id} (#19430)."""

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel

from app.api.deps import UserId
from app.clients.events import Events
from app.complaints import service
from app.complaints.validation import parse_complaint
from app.db import Session
from app.models import Complaint, ComplaintReason, ComplaintStatus, ComplaintTargetType

router = APIRouter(tags=["complaints"])

# Тело разбираем сами (validation.py): в OpenAPI это ComplaintCreate.
RawBody = Annotated[Any, Body()]


class ComplaintOut(BaseModel):
    """Жалоба глазами автора (схема Complaint в OpenAPI): без модератора, принявшего решение."""

    id: uuid.UUID
    target_type: ComplaintTargetType
    target_id: uuid.UUID
    target_version_id: uuid.UUID
    reason: ComplaintReason
    comment: str | None
    status: ComplaintStatus
    moderator_comment: str | None
    created_at: dt.datetime
    processed_at: dt.datetime | None

    @classmethod
    def of(cls, complaint: Complaint) -> "ComplaintOut":
        return cls(
            id=complaint.id,
            target_type=complaint.target_type,
            target_id=complaint.target_id,
            target_version_id=complaint.target_version_id,
            reason=complaint.reason,
            comment=complaint.text,
            status=complaint.status,
            moderator_comment=complaint.moderator_comment,
            created_at=complaint.created_at,
            processed_at=complaint.processed_at,
        )


class ComplaintPage(BaseModel):
    items: list[ComplaintOut]
    total: int
    page: int
    size: int


@router.post("/complaints", status_code=201)
async def create_complaint(
    user_id: UserId, db: Session, events: Events, body: RawBody
) -> ComplaintOut:
    new = parse_complaint(body)
    return ComplaintOut.of(await service.create(db, events, user_id, new))


@router.get("/me/complaints")
async def list_my_complaints(
    user_id: UserId,
    db: Session,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ComplaintPage:
    rows, total = await service.list_mine(db, user_id, page=page, size=size)
    return ComplaintPage(
        items=[ComplaintOut.of(row) for row in rows], total=total, page=page, size=size
    )


@router.get("/me/complaints/{id}")
async def get_my_complaint(user_id: UserId, db: Session, id: uuid.UUID) -> ComplaintOut:
    return ComplaintOut.of(await service.get_mine(db, user_id, id))

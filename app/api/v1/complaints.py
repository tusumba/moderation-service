"""POST /v1/complaints по OpenAPI Moderation (#19429)."""

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body
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


@router.post("/complaints", status_code=201)
async def create_complaint(
    user_id: UserId, db: Session, events: Events, body: RawBody
) -> ComplaintOut:
    new = parse_complaint(body)
    return ComplaintOut.of(await service.create(db, events, user_id, new))

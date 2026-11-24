"""Очередь заявок, карточка и решение модератора по OpenAPI (#19050)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query

from app.api.deps import ModeratorId
from app.api.v1.applications import ApplicationOut, ApplicationPage
from app.db import Session
from app.models import ApplicationStatus, ApplicationType
from app.moderation import service
from app.moderation.validation import parse_decision

router = APIRouter(prefix="/moderation", tags=["moderation-applications"])

# Тело разбираем сами (validation.py): в OpenAPI это ApplicationDecisionRequest.
RawBody = Annotated[Any, Body()]


@router.get("/applications")
async def list_applications(
    _: ModeratorId,
    db: Session,
    status: ApplicationStatus = "on_review",
    type_: Annotated[ApplicationType | None, Query(alias="type")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApplicationPage:
    rows, total = await service.list_queue(db, status=status, type_=type_, page=page, size=size)
    return ApplicationPage(
        items=[ApplicationOut.of(row) for row in rows], total=total, page=page, size=size
    )


@router.get("/applications/{id}")
async def get_application(_: ModeratorId, db: Session, id: uuid.UUID) -> ApplicationOut:
    return ApplicationOut.of(await service.get(db, id))


@router.post("/applications/{id}/decision")
async def decide_application(
    moderator_id: ModeratorId, db: Session, id: uuid.UUID, body: RawBody
) -> ApplicationOut:
    decision = parse_decision(body)
    return ApplicationOut.of(await service.decide(db, moderator_id, id, decision))

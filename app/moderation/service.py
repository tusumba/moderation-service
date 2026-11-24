"""Очередь заявок, карточка и решение модератора (#19050): требования #18106 и #18108."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tusumba_contracts import (
    ApplicationApproved,
    ApplicationRejected,
    BaseEvent,
    ClubPageApplicationData,
    EventApplicationData,
)

from app.applications import errors as application_errors
from app.events import enqueue_event
from app.models import Application, ApplicationStatus, ApplicationType, ModerationLogEntry
from app.moderation import errors
from app.moderation.validation import Approve, Decision


async def list_queue(
    db: AsyncSession,
    *,
    status: ApplicationStatus,
    type_: ApplicationType | None,
    page: int,
    size: int,
) -> tuple[list[Application], int]:
    """Заявки для модератора, старые первыми: первой рассматривается та, что ждёт дольше."""
    conditions = [Application.status == status]
    if type_ is not None:
        conditions.append(Application.type == type_)

    total = await db.scalar(select(func.count()).select_from(Application).where(*conditions))
    rows = await db.scalars(
        select(Application)
        .where(*conditions)
        .order_by(Application.created_at, Application.id)
        .offset((page - 1) * size)
        .limit(size)
    )
    return list(rows), total or 0


async def get(db: AsyncSession, application_id: uuid.UUID) -> Application:
    application = await db.get(Application, application_id)
    if application is None:
        raise application_errors.application_not_found()
    return application


async def decide(
    db: AsyncSession, moderator_id: uuid.UUID, application_id: uuid.UUID, decision: Decision
) -> Application:
    """Сохраняет решение, запись журнала и событие в outbox одной транзакцией.

    Строка заявки блокируется до коммита: если два модератора решают одновременно, второй
    дождётся первого и получит 409 (#18106, п. 5.2).
    """
    application = await db.scalar(
        select(Application).where(Application.id == application_id).with_for_update()
    )
    if application is None:
        raise application_errors.application_not_found()
    if application.status != "on_review":
        raise errors.application_already_processed()

    now = datetime.now(UTC)
    application.processed_by = moderator_id
    application.processed_at = now
    application.moderator_comment = decision.comment
    event: BaseEvent
    if isinstance(decision, Approve):
        application.status = "approved"
        # id будущего мероприятия или секции: events-service создаёт объект с этим id
        application.result_object_id = uuid.uuid4()
        event = _approved(application, application.result_object_id, moderator_id, now)
    else:
        application.status = "rejected"
        event = ApplicationRejected(
            application_id=application.id,
            type=application.type,
            author_user_id=application.author_user_id,
            club_id=application.club_id,
            moderator_comment=decision.comment,
            processed_by=moderator_id,
            processed_at=now,
        )

    db.add(
        ModerationLogEntry(
            moderator_id=moderator_id,
            action_type="application",
            object_id=application.id,
            decision=application.status,
            comment=decision.comment,
        )
    )
    enqueue_event(db, event)
    await db.commit()
    return application


def _approved(
    application: Application, result_object_id: uuid.UUID, moderator_id: uuid.UUID, now: datetime
) -> ApplicationApproved:
    """Данные заявки передаются в событии: по ним events-service создаёт объект."""
    is_event = application.type == "event"
    return ApplicationApproved(
        application_id=application.id,
        type=application.type,
        author_user_id=application.author_user_id,
        club_id=application.club_id,
        event=EventApplicationData.model_validate(application.payload) if is_event else None,
        club_page=None if is_event else ClubPageApplicationData.model_validate(application.payload),
        result_object_id=result_object_id,
        processed_by=moderator_id,
        processed_at=now,
    )

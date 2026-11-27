"""Подача жалобы (#19429) и «мои жалобы» (#19430): требования #18120 и #18121."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.events import EventsClient, EventsUnavailable
from app.complaints import errors
from app.complaints.validation import NewComplaint
from app.models import COMPLAINT_DUPLICATE_INDEX, Complaint


async def create(
    db: AsyncSession, events: EventsClient, author_id: uuid.UUID, new: NewComplaint
) -> Complaint:
    """Сохраняет жалобу со статусом new и текущей версией текста объекта.

    Версия берётся у events-service в момент подачи: если страницу потом отредактируют,
    модератор всё равно увидит текст, на который пожаловались.
    """
    try:
        target = await events.target(new.target_type, new.target_id)
    except EventsUnavailable:
        raise errors.complaint_not_sent() from None
    if target is None:
        raise errors.target_unavailable()

    complaint = Complaint(
        target_type=new.target_type,
        target_id=new.target_id,
        target_version_id=target.current_version_id,
        author_user_id=author_id,
        reason=new.reason,
        text=new.comment,
    )
    db.add(complaint)
    try:
        await db.flush()
    except IntegrityError as exc:
        # частичный уникальный индекс по нерассмотренным жалобам; гонка двух отправок тоже здесь
        await db.rollback()
        if COMPLAINT_DUPLICATE_INDEX in str(exc.orig):
            raise errors.complaint_duplicate() from None
        raise
    await db.refresh(complaint)
    await db.commit()
    return complaint


async def list_mine(
    db: AsyncSession, author_id: uuid.UUID, *, page: int, size: int
) -> tuple[list[Complaint], int]:
    """Жалобы автора, новые первыми, и их общее число."""
    condition = Complaint.author_user_id == author_id
    total = await db.scalar(select(func.count()).select_from(Complaint).where(condition))
    rows = await db.scalars(
        select(Complaint)
        .where(condition)
        .order_by(Complaint.created_at.desc(), Complaint.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return list(rows), total or 0


async def get_mine(db: AsyncSession, author_id: uuid.UUID, complaint_id: uuid.UUID) -> Complaint:
    """Жалоба автора. Чужая жалоба - 404, чтобы не раскрывать её существование."""
    complaint = await db.scalar(
        select(Complaint).where(Complaint.id == complaint_id, Complaint.author_user_id == author_id)
    )
    if complaint is None:
        raise errors.complaint_not_found()
    return complaint

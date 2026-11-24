"""Подача заявки и «мои заявки» (#19049): требования #18090 и #18098."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tusumba_common import ApiError, PermissionsClient

from app.applications import errors
from app.applications.validation import NewApplication
from app.models import DUPLICATE_INDEX, Application, ApplicationStatus, ApplicationType


async def create(
    db: AsyncSession, permissions: PermissionsClient, author_id: uuid.UUID, new: NewApplication
) -> Application:
    """Сохраняет заявку со статусом on_review. Дубликат (#18090, п. 5.3) даёт 409."""
    if new.club_id is not None:
        await _check_club_admin(permissions, new.club_id, author_id)
    application = Application(
        type=new.type, author_user_id=author_id, club_id=new.club_id, payload=new.payload
    )
    db.add(application)
    try:
        await db.flush()
    except IntegrityError as exc:
        # частичный уникальный индекс по заявкам на проверке; гонка двух отправок тоже здесь
        await db.rollback()
        if DUPLICATE_INDEX in str(exc.orig):
            raise errors.application_duplicate() from None
        raise
    await db.refresh(application)
    await db.commit()
    return application


async def _check_club_admin(
    permissions: PermissionsClient, club_id: uuid.UUID, author_id: uuid.UUID
) -> None:
    """Заявку от имени секции подаёт только её администратор (#18102).

    Нет секции или она заблокирована - 404 club_not_found от events-service как есть.
    """
    try:
        club = await permissions.club_permissions(club_id, author_id)
    except ApiError as exc:
        if exc.status_code == 503:
            raise errors.application_not_sent() from None
        raise
    if not club.is_admin:
        raise errors.club_forbidden()


async def list_mine(
    db: AsyncSession,
    author_id: uuid.UUID,
    *,
    status: ApplicationStatus | None,
    type_: ApplicationType | None,
    page: int,
    size: int,
) -> tuple[list[Application], int]:
    """Заявки автора, новые первыми, и их общее число с учётом фильтров."""
    conditions = [Application.author_user_id == author_id]
    if status is not None:
        conditions.append(Application.status == status)
    if type_ is not None:
        conditions.append(Application.type == type_)

    total = await db.scalar(select(func.count()).select_from(Application).where(*conditions))
    rows = await db.scalars(
        select(Application)
        .where(*conditions)
        .order_by(Application.created_at.desc(), Application.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return list(rows), total or 0


async def get_mine(
    db: AsyncSession, author_id: uuid.UUID, application_id: uuid.UUID
) -> Application:
    """Заявка автора. Чужая заявка - 404, чтобы не раскрывать её существование."""
    application = await db.scalar(
        select(Application).where(
            Application.id == application_id, Application.author_user_id == author_id
        )
    )
    if application is None:
        raise errors.application_not_found()
    return application

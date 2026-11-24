import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ApplicationType = Literal["event", "club_page"]
APPLICATION_TYPES: tuple[ApplicationType, ...] = ("event", "club_page")

ApplicationStatus = Literal["on_review", "approved", "rejected"]
APPLICATION_STATUSES: tuple[ApplicationStatus, ...] = ("on_review", "approved", "rejected")

DUPLICATE_INDEX = "uq_applications_on_review_duplicate"


class Application(Base):
    """Заявка на мероприятие или страницу секции (#18090, #18098). Данные заявки - в payload."""

    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint(
            "status <> 'rejected' OR moderator_comment IS NOT NULL",
            name="rejected_has_comment",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    type: Mapped[ApplicationType] = mapped_column(Enum(*APPLICATION_TYPES, name="application_type"))
    author_user_id: Mapped[uuid.UUID] = mapped_column()
    club_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(*APPLICATION_STATUSES, name="application_status"),
        default="on_review",
        server_default="on_review",
    )
    moderator_comment: Mapped[str | None] = mapped_column(Text, default=None)
    result_object_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    processed_by: Mapped[uuid.UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


# Очередь модератора и «мои заявки», новые первыми.
Index("ix_applications_status_created_at", Application.status, Application.created_at)
Index(
    "ix_applications_author_user_id_created_at",
    Application.author_user_id,
    Application.created_at,
)

# Дубликат (#18090, п. 5.3): заявка того же автора и того же типа на проверке с тем же названием
# без учёта регистра, для мероприятия - и с той же датой. Индекс не даёт создать дубликат и при
# одновременной отправке двух одинаковых заявок.
Index(
    DUPLICATE_INDEX,
    Application.author_user_id,
    Application.type,
    text("lower(coalesce(payload ->> 'title', payload ->> 'name'))"),
    text("coalesce(payload ->> 'date', '')"),
    unique=True,
    postgresql_where=text("status = 'on_review'"),
)

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, Enum, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ComplaintTargetType = Literal["event", "club"]
COMPLAINT_TARGET_TYPES: tuple[ComplaintTargetType, ...] = ("event", "club")

ComplaintReason = Literal[
    "fraud", "misleading_info", "offensive", "prohibited_content", "spam", "other"
]
COMPLAINT_REASONS: tuple[ComplaintReason, ...] = (
    "fraud",
    "misleading_info",
    "offensive",
    "prohibited_content",
    "spam",
    "other",
)

# new - новая; extra_check - дополнительная проверка; false - не подтвердилась; true - подтвердилась
ComplaintStatus = Literal["new", "extra_check", "false", "true"]
COMPLAINT_STATUSES: tuple[ComplaintStatus, ...] = ("new", "extra_check", "false", "true")

COMPLAINT_DUPLICATE_INDEX = "uq_complaints_open_duplicate"


class Complaint(Base):
    """Жалоба на мероприятие или секцию (#18120, #18121). Текст объекта - по target_version_id."""

    __tablename__ = "complaints"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_type: Mapped[ComplaintTargetType] = mapped_column(
        Enum(*COMPLAINT_TARGET_TYPES, name="complaint_target_type")
    )
    target_id: Mapped[uuid.UUID] = mapped_column()
    target_version_id: Mapped[uuid.UUID] = mapped_column()
    author_user_id: Mapped[uuid.UUID] = mapped_column()
    reason: Mapped[ComplaintReason] = mapped_column(
        Enum(*COMPLAINT_REASONS, name="complaint_reason")
    )
    text: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[ComplaintStatus] = mapped_column(
        Enum(*COMPLAINT_STATUSES, name="complaint_status"),
        default="new",
        server_default="new",
    )
    processed_by: Mapped[uuid.UUID | None] = mapped_column(default=None)
    moderator_comment: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


# Очередь модератора; все жалобы на объект; «мои жалобы», новые первыми.
Index("ix_complaints_status_created_at", Complaint.status, Complaint.created_at)
Index("ix_complaints_target_type_target_id", Complaint.target_type, Complaint.target_id)
Index("ix_complaints_author_user_id_created_at", Complaint.author_user_id, Complaint.created_at)

# Повторная жалоба автора на объект до решения (#18120, ошибка 6.2) не создаётся,
# в том числе при одновременной отправке.
Index(
    COMPLAINT_DUPLICATE_INDEX,
    Complaint.author_user_id,
    Complaint.target_type,
    Complaint.target_id,
    unique=True,
    postgresql_where=text("status IN ('new', 'extra_check')"),
)

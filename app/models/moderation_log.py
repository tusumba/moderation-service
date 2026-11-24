import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, Enum, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ModerationActionType = Literal["application", "complaint"]
MODERATION_ACTION_TYPES: tuple[ModerationActionType, ...] = ("application", "complaint")

ModerationDecision = Literal["approved", "rejected", "confirmed", "extra_check"]
MODERATION_DECISIONS: tuple[ModerationDecision, ...] = (
    "approved",
    "rejected",
    "confirmed",
    "extra_check",
)


class ModerationLogEntry(Base):
    """Журнал решений модераторов (#18108). Строки только добавляются."""

    __tablename__ = "moderation_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    moderator_id: Mapped[uuid.UUID] = mapped_column()
    action_type: Mapped[ModerationActionType] = mapped_column(
        Enum(*MODERATION_ACTION_TYPES, name="moderation_action_type")
    )
    object_id: Mapped[uuid.UUID] = mapped_column()
    decision: Mapped[ModerationDecision] = mapped_column(
        Enum(*MODERATION_DECISIONS, name="moderation_decision")
    )
    comment: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Журнал - новые первыми; действия модератора; все решения по заявке или жалобе.
Index("ix_moderation_log_created_at", ModerationLogEntry.created_at)
Index(
    "ix_moderation_log_moderator_id_created_at",
    ModerationLogEntry.moderator_id,
    ModerationLogEntry.created_at,
)
Index(
    "ix_moderation_log_action_type_object_id",
    ModerationLogEntry.action_type,
    ModerationLogEntry.object_id,
)

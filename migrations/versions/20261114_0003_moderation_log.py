"""moderation_log

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "moderation"


def upgrade() -> None:
    op.create_table(
        "moderation_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("moderator_id", sa.Uuid(), nullable=False),
        sa.Column(
            "action_type",
            sa.Enum("application", "complaint", name="moderation_action_type", schema=SCHEMA),
            nullable=False,
        ),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "approved",
                "rejected",
                "confirmed",
                "extra_check",
                name="moderation_decision",
                schema=SCHEMA,
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_log")),
        schema=SCHEMA,
    )
    op.create_index("ix_moderation_log_created_at", "moderation_log", ["created_at"], schema=SCHEMA)
    op.create_index(
        "ix_moderation_log_moderator_id_created_at",
        "moderation_log",
        ["moderator_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_moderation_log_action_type_object_id",
        "moderation_log",
        ["action_type", "object_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("moderation_log", schema=SCHEMA)
    op.execute(f"DROP TYPE {SCHEMA}.moderation_decision")
    op.execute(f"DROP TYPE {SCHEMA}.moderation_action_type")

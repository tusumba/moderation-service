"""complaints

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "moderation"


def upgrade() -> None:
    op.create_table(
        "complaints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum("event", "club", name="complaint_target_type", schema=SCHEMA),
            nullable=False,
        ),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("target_version_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "fraud",
                "misleading_info",
                "offensive",
                "prohibited_content",
                "spam",
                "other",
                name="complaint_reason",
                schema=SCHEMA,
            ),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("new", "extra_check", "false", "true", name="complaint_status", schema=SCHEMA),
            server_default="new",
            nullable=False,
        ),
        sa.Column("processed_by", sa.Uuid(), nullable=True),
        sa.Column("moderator_comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_complaints")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_complaints_status_created_at", "complaints", ["status", "created_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_complaints_target_type_target_id",
        "complaints",
        ["target_type", "target_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_complaints_author_user_id_created_at",
        "complaints",
        ["author_user_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_complaints_open_duplicate",
        "complaints",
        ["author_user_id", "target_type", "target_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('new', 'extra_check')"),
    )


def downgrade() -> None:
    op.drop_table("complaints", schema=SCHEMA)
    op.execute(f"DROP TYPE {SCHEMA}.complaint_status")
    op.execute(f"DROP TYPE {SCHEMA}.complaint_reason")
    op.execute(f"DROP TYPE {SCHEMA}.complaint_target_type")

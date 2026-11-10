"""applications

Revision ID: 0001
Revises:
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "moderation"


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("event", "club_page", name="application_type", schema=SCHEMA),
            nullable=False,
        ),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("club_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("on_review", "approved", "rejected", name="application_status", schema=SCHEMA),
            server_default="on_review",
            nullable=False,
        ),
        sa.Column("moderator_comment", sa.Text(), nullable=True),
        sa.Column("result_object_id", sa.Uuid(), nullable=True),
        sa.Column("processed_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status <> 'rejected' OR moderator_comment IS NOT NULL",
            name=op.f("ck_applications_rejected_has_comment"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applications")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_applications_status_created_at",
        "applications",
        ["status", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_applications_author_user_id_created_at",
        "applications",
        ["author_user_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_applications_on_review_duplicate",
        "applications",
        [
            "author_user_id",
            "type",
            sa.text("lower(coalesce(payload ->> 'title', payload ->> 'name'))"),
            sa.text("coalesce(payload ->> 'date', '')"),
        ],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'on_review'"),
    )


def downgrade() -> None:
    op.drop_table("applications", schema=SCHEMA)
    op.execute(f"DROP TYPE {SCHEMA}.application_status")
    op.execute(f"DROP TYPE {SCHEMA}.application_type")

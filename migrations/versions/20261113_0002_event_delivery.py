"""outbox_messages, processed_events (доставка событий, #19053)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "moderation"


def upgrade() -> None:
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("routing_key", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_messages")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_outbox_messages_pending",
        "outbox_messages",
        ["created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_processed_events")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("processed_events", schema=SCHEMA)
    op.drop_index("ix_outbox_messages_pending", table_name="outbox_messages", schema=SCHEMA)
    op.drop_table("outbox_messages", schema=SCHEMA)

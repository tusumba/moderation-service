import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from faststream.rabbit import RabbitBroker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tusumba_contracts import ApplicationRejected, BaseEvent, EventEnvelope

from app.db import create_engine, create_sessionmaker
from app.events import enqueue_event, publish_pending, subscribe_event
from app.models import OutboxMessage, ProcessedEvent
from app.settings import Settings


def rejected_event() -> ApplicationRejected:
    return ApplicationRejected(
        application_id=uuid.uuid4(),
        type="event",
        author_user_id=uuid.uuid4(),
        moderator_comment="Отклонено",
        processed_by=uuid.uuid4(),
        processed_at=datetime.now(UTC),
    )


async def test_committed_outbox_is_published_and_duplicate_is_ignored(
    settings: Settings, rabbitmq_url: str
) -> None:
    engine = create_engine(settings)
    sessions = create_sessionmaker(engine)
    event = rejected_event()
    consumed = asyncio.Event()
    processed: list[uuid.UUID] = []

    async def handle(session: AsyncSession, envelope: EventEnvelope[BaseEvent]) -> None:
        processed.append(envelope.event_id)
        consumed.set()

    try:
        async with sessions.begin() as session:
            event_id = enqueue_event(session, event)

        async with sessions() as session:
            row = await session.get(OutboxMessage, event_id)
            assert row is not None and row.published_at is None

        broker = RabbitBroker(rabbitmq_url)
        subscribe_event(
            broker,
            sessions,
            queue="moderation.applications",
            routing_key=ApplicationRejected.routing_key,
            handler=handle,
        )
        await broker.start()
        try:
            assert await publish_pending(sessions, broker) == 1
            await asyncio.wait_for(consumed.wait(), timeout=10)

            async def processed_marker_committed() -> None:
                while True:
                    async with sessions() as session:
                        if await session.get(ProcessedEvent, event_id) is not None:
                            return
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(processed_marker_committed(), timeout=10)
            async with sessions() as session:
                row = await session.get(OutboxMessage, event_id)
                assert row is not None and row.published_at is not None

            async with sessions.begin() as session:
                row = await session.get(OutboxMessage, event_id)
                assert row is not None
                row.published_at = None
            assert await publish_pending(sessions, broker) == 1
            await asyncio.sleep(0.3)
            assert processed == [event_id]
        finally:
            await broker.stop()
    finally:
        await engine.dispose()


async def test_failed_handler_rolls_back_deduplication_and_domain_change(
    settings: Settings,
) -> None:
    engine = create_engine(settings)
    sessions = create_sessionmaker(engine)
    event = rejected_event().envelope()
    message = event.model_dump(mode="json")
    event_id = event.event_id
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE moderation.delivery_probe (event_id uuid PRIMARY KEY)")
            )

        async def fails(session: AsyncSession, envelope: EventEnvelope[BaseEvent]) -> None:
            await session.execute(
                text("INSERT INTO delivery_probe (event_id) VALUES (:id)"), {"id": event_id}
            )
            raise RuntimeError("handler failed")

        with pytest.raises(RuntimeError, match="handler failed"):
            from app.events import process_event

            await process_event(sessions, message, fails)

        async with sessions() as session:
            assert await session.get(ProcessedEvent, event_id) is None
            assert await session.scalar(text("SELECT count(*) FROM delivery_probe")) == 0

        async def succeeds(session: AsyncSession, envelope: EventEnvelope[BaseEvent]) -> None:
            await session.execute(
                text("INSERT INTO delivery_probe (event_id) VALUES (:id)"), {"id": event_id}
            )

        from app.events import process_event

        assert await process_event(sessions, message, succeeds) is True
        assert await process_event(sessions, message, succeeds) is False
        async with sessions() as session:
            assert await session.get(ProcessedEvent, event_id) is not None
            assert await session.scalar(text("SELECT count(*) FROM delivery_probe")) == 1
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS moderation.delivery_probe"))
        await engine.dispose()

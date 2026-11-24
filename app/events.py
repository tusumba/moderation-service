import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from faststream import AckPolicy
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tusumba_contracts import EXCHANGE, BaseEvent, EventEnvelope, parse_envelope

from app.models import OutboxMessage, ProcessedEvent

EVENT_EXCHANGE = RabbitExchange(EXCHANGE, type=ExchangeType.TOPIC, durable=True)
EventHandler = Callable[[AsyncSession, EventEnvelope[BaseEvent]], Awaitable[None]]


def enqueue_event(session: AsyncSession, event: BaseEvent) -> uuid.UUID:
    envelope = event.envelope()
    session.add(
        OutboxMessage(
            id=envelope.event_id,
            routing_key=event.routing_key,
            payload=envelope.model_dump(mode="json"),
        )
    )
    return envelope.event_id


async def publish_pending(
    sessions: async_sessionmaker[AsyncSession], broker: RabbitBroker, *, limit: int = 100
) -> int:
    published = 0
    for _ in range(limit):
        async with sessions.begin() as session:
            message = await session.scalar(
                select(OutboxMessage)
                .where(OutboxMessage.published_at.is_(None))
                .order_by(OutboxMessage.created_at, OutboxMessage.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if message is None:
                break
            await broker.publish(
                message.payload,
                routing_key=message.routing_key,
                exchange=EVENT_EXCHANGE,
                mandatory=True,
                persist=True,
                message_id=str(message.id),
            )
            message.published_at = datetime.now(UTC)
        published += 1
    return published


async def process_event(
    sessions: async_sessionmaker[AsyncSession],
    message: dict[str, Any],
    handler: EventHandler,
) -> bool:
    envelope = parse_envelope(message)
    async with sessions.begin() as session:
        inserted = await session.scalar(
            insert(ProcessedEvent)
            .values(event_id=envelope.event_id)
            .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id])
            .returning(ProcessedEvent.event_id)
        )
        if inserted is None:
            return False
        await handler(session, envelope)
    return True


def subscribe_event(
    broker: RabbitBroker,
    sessions: async_sessionmaker[AsyncSession],
    *,
    queue: str,
    routing_key: str,
    handler: EventHandler,
) -> None:
    @broker.subscriber(
        RabbitQueue(queue, durable=True, routing_key=routing_key),
        EVENT_EXCHANGE,
        ack_policy=AckPolicy.NACK_ON_ERROR,
    )
    async def receive(message: dict[str, Any]) -> None:
        await process_event(sessions, message, handler)

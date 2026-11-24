from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from faststream.rabbit import RabbitBroker
from tusumba_common import install_error_handlers

from app.api import health, v1
from app.db import create_engine, create_sessionmaker
from app.events import publish_pending
from app.logging import configure_logging
from app.middleware import RequestIdMiddleware
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.service_name, settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.sessionmaker = create_sessionmaker(engine)
        broker = RabbitBroker(settings.rabbitmq_url.get_secret_value())
        app.state.broker = broker
        scheduler = AsyncIOScheduler()
        try:
            await broker.start()
            scheduler.add_job(
                publish_pending,
                "interval",
                seconds=settings.outbox_poll_seconds,
                args=[app.state.sessionmaker, broker],
                max_instances=1,
                coalesce=True,
            )
            scheduler.start()
            yield
        finally:
            if scheduler.running:
                scheduler.shutdown(wait=True)
            await broker.stop()
            await engine.dispose()

    app = FastAPI(title=settings.service_name, lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health.router)
    app.include_router(v1.router)
    return app

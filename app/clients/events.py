"""Клиент events-service: объект жалобы и его текущая версия текста (OpenAPI Events, #19428).

GET /internal/events/{id} и GET /internal/clubs/{id} возвращают current_version_id: эту версию
Moderation сохраняет в жалобе, чтобы модератор видел текст на момент подачи (#18120).
"""

import logging
import uuid
from typing import Annotated

import httpx
from fastapi import Depends, Request
from pydantic import BaseModel, ValidationError
from tusumba_common import InternalClient

from app.models import ComplaintTargetType

logger = logging.getLogger(__name__)


class EventsUnavailable(Exception):
    """events-service не ответил, ответил ошибкой или неожиданным телом."""


class Target(BaseModel):
    """Поля EventInternal и ClubInternal, нужные для жалобы."""

    id: uuid.UUID
    current_version_id: uuid.UUID
    status: str


PATHS: dict[ComplaintTargetType, str] = {"event": "/internal/events", "club": "/internal/clubs"}


class EventsClient:
    """events = EventsClient("http://events:8000"); target = await events.target("event", id)"""

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._http = client or InternalClient(base_url=base_url)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def target(self, target_type: ComplaintTargetType, target_id: uuid.UUID) -> Target | None:
        """Мероприятие или секция; None, если объекта нет, он удалён или секция заблокирована."""
        path = f"{PATHS[target_type]}/{target_id}"
        try:
            response = await self._http.get(path)
        except httpx.HTTPError:
            logger.warning("events-service не ответил на %s", path, exc_info=True)
            raise EventsUnavailable from None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            # 403 здесь значит неверный SERVICE_TOKEN: ошибка настройки, а не пользователя
            logger.error("events-service ответил %s на %s", response.status_code, path)
            raise EventsUnavailable
        try:
            target = Target.model_validate_json(response.content)
        except ValidationError:
            logger.error("events-service вернул неожиданный ответ на %s", path)
            raise EventsUnavailable from None
        # /internal/clubs/{id} отдаёт и заблокированную секцию (OpenAPI Events)
        return None if target.status == "blocked" else target


def get_events_client(request: Request) -> EventsClient:
    client: EventsClient = request.app.state.events_client
    return client


Events = Annotated[EventsClient, Depends(get_events_client)]

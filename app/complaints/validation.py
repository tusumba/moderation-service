"""Проверка тела POST /v1/complaints с текстами из требования #18120."""

import uuid
from dataclasses import dataclass
from typing import Any

from app.applications import errors as common_errors
from app.complaints import errors
from app.models import (
    COMPLAINT_REASONS,
    COMPLAINT_TARGET_TYPES,
    ComplaintReason,
    ComplaintTargetType,
)

COMMENT_MAX = 1000
TARGET_TYPE_UNKNOWN = "Выберите объект жалобы: event или club."


@dataclass(frozen=True)
class NewComplaint:
    target_type: ComplaintTargetType
    target_id: uuid.UUID
    reason: ComplaintReason
    comment: str | None


def parse_complaint(body: Any) -> NewComplaint:
    """Возвращает данные жалобы или бросает 422 с ошибками по всем полям сразу."""
    if not isinstance(body, dict):
        raise common_errors.validation_error(
            common_errors.INVALID_DATA_MESSAGE,
            [{"field": "body", "message": "Ожидается JSON-объект."}],
        )
    details: list[dict[str, str]] = []
    missing = False

    target_type = body.get("target_type")
    if target_type is None or target_type == "":
        missing = True
        details.append({"field": "target_type", "message": TARGET_TYPE_UNKNOWN})
    elif target_type not in COMPLAINT_TARGET_TYPES:
        details.append({"field": "target_type", "message": TARGET_TYPE_UNKNOWN})

    target_id: uuid.UUID | None = None
    raw_id = body.get("target_id")
    if raw_id is None or raw_id == "":
        missing = True
        details.append({"field": "target_id", "message": "Заполните обязательное поле."})
    else:
        try:
            target_id = uuid.UUID(raw_id) if isinstance(raw_id, str) else None
        except ValueError:
            target_id = None
        if target_id is None:
            details.append({"field": "target_id", "message": "Некорректное значение."})

    reason: Any = body.get("reason")
    if reason not in COMPLAINT_REASONS:
        # без причины и с неизвестной причиной - один и тот же текст требования
        details.append({"field": "reason", "message": errors.REASON_REQUIRED})

    comment = body.get("comment")
    if comment is not None and not isinstance(comment, str):
        details.append({"field": "comment", "message": "Комментарий должен быть строкой."})
        comment = None
    comment = comment.strip() if comment and comment.strip() else None
    if comment is not None and len(comment) > COMMENT_MAX:
        details.append({"field": "comment", "message": f"Не больше {COMMENT_MAX} символов."})

    if details or target_id is None:  # без target_id в details уже есть ошибка
        if any(detail["field"] == "reason" for detail in details):
            message = errors.REASON_REQUIRED
        elif missing:
            message = common_errors.REQUIRED_MESSAGE
        elif len(details) == 1:
            message = details[0]["message"]
        else:
            message = common_errors.INVALID_DATA_MESSAGE
        raise common_errors.validation_error(message, details)

    return NewComplaint(
        target_type="event" if target_type == "event" else "club",
        target_id=target_id,
        reason=reason,
        comment=comment,
    )

"""Проверка тела POST /v1/moderation/applications/{id}/decision.

Как и в подаче заявки, поля проверяет сам сервис: клиент получает тексты из требования #18106.
"""

from dataclasses import dataclass
from typing import Any, Literal

from app.applications import errors

DecisionKind = Literal["approve", "reject"]
DECISIONS: tuple[DecisionKind, ...] = ("approve", "reject")
COMMENT_MAX = 1000

DECISION_UNKNOWN = "Выберите решение: approve или reject."
COMMENT_REQUIRED = "Укажите комментарий к отказу."
COMMENT_REQUIRED_DETAIL = "Комментарий обязателен при отклонении заявки."


@dataclass(frozen=True)
class Approve:
    comment: str | None


@dataclass(frozen=True)
class Reject:
    comment: str


Decision = Approve | Reject


def parse_decision(body: Any) -> Decision:
    """Решение модератора или 422. Отклонить без комментария нельзя (#18106, п. 4.10)."""
    if not isinstance(body, dict):
        raise errors.validation_error(
            errors.INVALID_DATA_MESSAGE, [{"field": "body", "message": "Ожидается JSON-объект."}]
        )

    kind = body.get("decision")
    if kind is None or kind == "":
        raise errors.validation_error(
            errors.REQUIRED_MESSAGE, [{"field": "decision", "message": DECISION_UNKNOWN}]
        )
    if kind not in DECISIONS:
        raise errors.validation_error(
            DECISION_UNKNOWN, [{"field": "decision", "message": DECISION_UNKNOWN}]
        )

    raw = body.get("comment")
    if raw is not None and not isinstance(raw, str):
        raise errors.validation_error(
            errors.INVALID_DATA_MESSAGE,
            [{"field": "comment", "message": "Комментарий должен быть строкой."}],
        )
    # Комментарий из одних пробелов - то же, что его нет.
    comment = raw.strip() if raw is not None and raw.strip() else None
    if comment is not None and len(comment) > COMMENT_MAX:
        message = f"Не больше {COMMENT_MAX} символов."
        raise errors.validation_error(message, [{"field": "comment", "message": message}])
    if kind == "approve":
        return Approve(comment=comment)
    if comment is None:
        raise errors.validation_error(
            COMMENT_REQUIRED, [{"field": "comment", "message": COMMENT_REQUIRED_DETAIL}]
        )
    return Reject(comment=comment)

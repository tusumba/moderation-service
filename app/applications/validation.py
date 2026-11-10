"""Проверка тела POST /v1/applications.

Поля проверяет сам сервис, а не pydantic: так клиент получает тексты из требований #18090
и #18098 вместо общего текста валидации tusumba-common (см. OpenAPI). Ошибки собираются
по всем полям сразу, чтобы клиент показал их у каждого поля.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, NoReturn

from app.applications import errors
from app.models import APPLICATION_TYPES, ApplicationType

TITLE_MAX = 255
DESCRIPTION_MAX = 1000
PLACE_MAX = 500
SCHEDULE_MAX = 1000
CURRENCIES = ("RUB",)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REQUIRED_FIELD = "Заполните обязательное поле."
INVALID_VALUE = "Некорректное значение."
PLACE_REQUIRED = "Укажите место проведения."
DATE_IN_PAST = "Дата мероприятия не может быть в прошлом."
DATE_FORMAT = "Укажите дату в формате ГГГГ-ММ-ДД."
PRICE_REQUIRED = "Укажите цену платного мероприятия."
TYPE_UNKNOWN = "Выберите тип заявки: event или club_page."
BLOCK_MISMATCH = "Блок не соответствует типу заявки."
CLUB_NOT_SUPPORTED = "Заявка от имени секции пока недоступна."


@dataclass(frozen=True)
class NewApplication:
    type: ApplicationType
    payload: dict[str, Any]


class _Problems:
    """Ошибки по полям для ответа 422 в формате tusumba-common."""

    def __init__(self) -> None:
        self.details: list[dict[str, str]] = []
        self.has_missing = False

    def missing(self, field: str, message: str = REQUIRED_FIELD) -> None:
        self.has_missing = True
        self.details.append({"field": field, "message": message})

    def invalid(self, field: str, message: str = INVALID_VALUE) -> None:
        self.details.append({"field": field, "message": message})

    def raise_if_any(self) -> None:
        if self.details:
            self.fail()

    def fail(self) -> NoReturn:
        if self.has_missing:
            message = errors.REQUIRED_MESSAGE  # #18098, ошибка 6.1
        elif len(self.details) == 1:
            message = self.details[0]["message"]
        else:
            message = errors.INVALID_DATA_MESSAGE
        raise errors.validation_error(message, self.details)

    def text(
        self,
        data: dict[str, Any],
        name: str,
        field: str,
        max_length: int,
        *,
        required: bool = True,
        missing_message: str = REQUIRED_FIELD,
    ) -> str | None:
        """Строка без пробелов по краям. Пустая строка - то же, что поле не заполнено."""
        value = data.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                self.missing(field, missing_message)
            return None
        if not isinstance(value, str):
            self.invalid(field)
            return None
        value = value.strip()
        if len(value) > max_length:
            self.invalid(field, f"Не больше {max_length} символов.")
            return None
        return value


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise errors.validation_error(
            errors.INVALID_DATA_MESSAGE, [{"field": field, "message": "Ожидается JSON-объект."}]
        )
    return value


def _event_date(block: dict[str, Any], today: date, problems: _Problems) -> str | None:
    value = block.get("date")
    if value is None or value == "":
        problems.missing("event.date")
        return None
    if not isinstance(value, str) or not ISO_DATE.match(value):
        problems.invalid("event.date", DATE_FORMAT)
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        problems.invalid("event.date", DATE_FORMAT)
        return None
    if parsed < today:
        problems.invalid("event.date", DATE_IN_PAST)  # #18098, ошибка 6.2
        return None
    return parsed.isoformat()


def _price(value: Any, problems: _Problems) -> dict[str, Any] | None:
    if value is None:
        problems.missing("event.price", PRICE_REQUIRED)
        return None
    if not isinstance(value, dict):
        problems.invalid("event.price")
        return None
    amount = value.get("amount")
    currency = value.get("currency")
    if amount is None:
        problems.missing("event.price.amount")
    elif isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        problems.invalid("event.price.amount", "Сумма в копейках: целое число от 0.")
    if currency is None:
        problems.missing("event.price.currency")
    elif currency not in CURRENCIES:
        problems.invalid("event.price.currency", "Валюта: RUB.")
    return {"amount": amount, "currency": currency}


def _event(block: dict[str, Any], today: date, problems: _Problems) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": problems.text(block, "title", "event.title", TITLE_MAX),
        "description": problems.text(block, "description", "event.description", DESCRIPTION_MAX),
        "date": _event_date(block, today, problems),
        "place_address": problems.text(
            block,
            "place_address",
            "event.place_address",
            PLACE_MAX,
            missing_message=PLACE_REQUIRED,  # #18098, ошибка 6.3
        ),
    }
    is_free = block.get("is_free")
    if is_free is None:
        problems.missing("event.is_free")
    elif not isinstance(is_free, bool):
        problems.invalid("event.is_free")
    payload["is_free"] = is_free
    if is_free is False:  # цена бесплатного мероприятия не сохраняется
        payload["price"] = _price(block.get("price"), problems)
    return payload


def _contacts(value: Any, problems: _Problems) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        problems.invalid("club_page.contacts", "Контакты: канал связи и адрес строкой.")
        return None
    contacts = {key: address.strip() for key, address in value.items() if address.strip()}
    return contacts or None


def _club_page(block: dict[str, Any], problems: _Problems) -> dict[str, Any]:
    payload = {
        "name": problems.text(block, "name", "club_page.name", TITLE_MAX),
        "description": problems.text(
            block, "description", "club_page.description", DESCRIPTION_MAX
        ),
        "meeting_place": problems.text(
            block, "meeting_place", "club_page.meeting_place", PLACE_MAX, required=False
        ),
        "schedule": problems.text(
            block, "schedule", "club_page.schedule", SCHEDULE_MAX, required=False
        ),
        "contacts": _contacts(block.get("contacts"), problems),
    }
    return {key: value for key, value in payload.items() if value is not None}


def parse_application(body: Any, today: date) -> NewApplication:
    """Возвращает тип и данные заявки для payload или бросает 422."""
    data = _object(body, "body")
    problems = _Problems()

    # Заявка от имени секции (#18102) требует проверки прав в events-service: не в этой задаче.
    if data.get("club_id") is not None:
        problems.invalid("club_id", CLUB_NOT_SUPPORTED)

    kind = data.get("type")
    if kind is None or kind == "":
        problems.missing("type")
    elif kind not in APPLICATION_TYPES:
        problems.invalid("type", TYPE_UNKNOWN)
    problems.raise_if_any()

    application_type: ApplicationType = "event" if kind == "event" else "club_page"
    other = "club_page" if application_type == "event" else "event"
    if data.get(other) is not None:
        problems.invalid(other, BLOCK_MISMATCH)

    block = data.get(application_type)
    if block is None:
        problems.missing(application_type)
        problems.fail()
    if not isinstance(block, dict):
        problems.invalid(application_type, "Ожидается JSON-объект.")
        problems.fail()

    if application_type == "event":
        payload = _event(block, today, problems)
    else:
        payload = _club_page(block, problems)
    problems.raise_if_any()
    return NewApplication(type=application_type, payload=payload)

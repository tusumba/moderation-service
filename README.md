# moderation-service

Сервис **Moderation** системы Tusumba: заявки на мероприятия и страницы клубов, жалобы, решения модераторов, журнал действий модерации.

**Ответственный:** Михаил Коротков (задача #18890).

## Что делает

- приём заявок на мероприятие и на страницу клуба, очередь и карточка заявки для модератора, решение (комментарий обязателен при отказе);
- при одобрении публикует событие `ApplicationApproved`, по которому `events-service` создаёт объект;
- приём жалоб на мероприятие и клуб: при подаче запрашивает у `events-service` текст на момент жалобы и фиксирует версию;
- решения по жалобам: подтвердить, отклонить, запросить дополнительную проверку;
- журнал действий модерации с фильтрами.

База данных `moderation_db`: `applications`, `complaints`, `moderation_log` (в миграциях пока `applications`).

## API

Внешний: `POST /v1/applications`, `GET /v1/me/applications`, `GET /v1/moderation/applications`, `POST /v1/moderation/applications/{id}/decision`, `POST /v1/complaints`, `GET /v1/me/complaints`, `GET /v1/moderation/complaints`, `POST /v1/moderation/complaints/{id}/decision`, `GET /v1/moderation/log`.

События в брокер: `ApplicationApproved`, `ApplicationRejected`, `ComplaintResolved`.

Зависит от: `events-service` (права и версии текстов).

Закрывает требования: #18090, #18098 (подача заявки на мероприятие), #18106, #18107, #18108, #18120, #18121.

## Стек

Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 16, RabbitMQ (FastStream), tusumba-common (проверка JWT, сервисный токен, формат ошибок).

## Запуск

Нужны [uv](https://docs.astral.sh/uv/) и Docker. Сервис собран из [service-template](https://github.com/tusumba/service-template).

```bash
uv sync
cp .env.example .env
uv run python -m tusumba_common.testing dev-keys   # тестовая пара ключей, впишите public.pem в JWT_PUBLIC_KEY
docker compose up -d postgres
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:create_app --factory --reload
```

Документация API: http://127.0.0.1:8000/docs. Окружение целиком поднимается из репозитория `deploy`.

### Переменные окружения

| Переменная | По умолчанию | Что это |
|---|---|---|
| `DATABASE_URL` | обязательна | `postgresql+asyncpg://user:password@host:5432/db` |
| `JWT_PUBLIC_KEY` | обязательна | публичный ключ Identity в PEM, tusumba-common проверяет им access-токены |
| `SERVICE_TOKEN` | обязательна для вызовов `/internal` других сервисов | общий сервисный токен для `X-Service-Token` |
| `LOG_LEVEL` | `INFO` | уровень логов |
| `LOG_JSON` | `true` | `false` - читаемые логи для отладки |

### Проверки

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run pytest      # PostgreSQL поднимается через testcontainers (нужен Docker)
```

Без Docker тесты идут на уже запущенном PostgreSQL: `TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest`.

## Документация

- [OpenAPI v1](docs/openapi.yaml) - API разделов «Заявки и модерация» и «Жалобы» архитектуры.
- [Модель данных](docs/data-model.md) - таблицы заявок, жалоб и журнала модерации, события, коды ошибок.

Wiki проекта: https://ai.nsu.ru/projects/tusumba/wiki/Архитектура. Контракт API: OpenAPI v1 (#18890). Задачи ведутся в Redmine: https://ai.nsu.ru/projects/tusumba. Issues и Wiki GitHub выключены.

## Правила работы

Ветки, коммиты, pull request и code review описаны в [CONTRIBUTING.md](https://github.com/tusumba/.github/blob/main/CONTRIBUTING.md). Коротко:

- ветка называется `feature/<номер задачи Redmine>-<пара слов>`, например `feature/18901-login`;
- номер задачи указывается в каждом коммите и в названии PR;
- в `main` изменения попадают только через PR с одним одобрением и зелёным CI, прямой push и force push запрещены.

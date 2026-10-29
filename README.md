# moderation-service

Сервис **Moderation** системы Tusumba: заявки на мероприятия и страницы клубов, жалобы, решения модераторов, журнал действий модерации.

**Ответственный:** Михаил Коротков (задача #18890).

## Что делает

- приём заявок на мероприятие и на страницу клуба, очередь и карточка заявки для модератора, решение (комментарий обязателен при отказе);
- при одобрении публикует событие `ApplicationApproved`, по которому `events-service` создаёт объект;
- приём жалоб на мероприятие и клуб: при подаче запрашивает у `events-service` текст на момент жалобы и фиксирует версию;
- решения по жалобам: подтвердить, отклонить, запросить дополнительную проверку;
- журнал действий модерации с фильтрами.

База данных `moderation_db`: `applications`, `complaints`, `moderation_log`.

## API

Внешний: `POST /v1/applications`, `GET /v1/me/applications`, `GET /v1/moderation/applications`, `POST /v1/moderation/applications/{id}/decision`, `POST /v1/complaints`, `GET /v1/me/complaints`, `GET /v1/moderation/complaints`, `POST /v1/moderation/complaints/{id}/decision`, `GET /v1/moderation/log`.

События в брокер: `ApplicationApproved`, `ComplaintResolved`.

Зависит от: `events-service` (права и версии текстов).

Закрывает требования: #18090, #18098 (подача заявки на мероприятие), #18106, #18107, #18108, #18120, #18121.

## Стек

Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 16, RabbitMQ (FastStream).

## Запуск

Описание появится после готовности шаблона сервиса (#18887) и стенда (#18905). Окружение целиком поднимается из репозитория `deploy`.

## Документация

Wiki проекта: https://ai.nsu.ru/projects/tusumba/wiki/Архитектура. Контракт API: OpenAPI v1 (#18890). Задачи ведутся в Redmine: https://ai.nsu.ru/projects/tusumba. Issues и Wiki GitHub выключены.

## Правила работы

Ветки, коммиты, pull request и code review описаны в [CONTRIBUTING.md](https://github.com/tusumba/.github/blob/main/CONTRIBUTING.md). Коротко:

- ветка называется `feature/<номер задачи Redmine>-<пара слов>`, например `feature/18901-login`;
- номер задачи указывается в каждом коммите и в названии PR;
- в `main` изменения попадают только через PR с одним одобрением и зелёным CI, прямой push и force push запрещены.

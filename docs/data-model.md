# Модель данных Moderation

Задача: [#18890 - OpenAPI v1 и модель данных: Moderation](https://ai.nsu.ru/issues/18890).
Источник: [архитектура Tusumba](https://ai.nsu.ru/projects/tusumba/wiki/Архитектура),
раздел «Moderation (moderation_db)» и [ER-диаграмма](https://ai.nsu.ru/attachments/download/6817/er-moderation-db.png).

Схема PostgreSQL сервиса - `moderation`. Идентификаторы - `uuid`, время - `timestamptz` в UTC.

## applications

Заявки на мероприятие и на страницу секции (#18090, #18098, #18102, #18106).

| Поле | Тип | Ключ |
|---|---|---|
| id | uuid | PK |
| type | enum (event, club_page) | |
| author_user_id | uuid | |
| club_id | uuid, null | |
| payload | jsonb | |
| status | enum (on_review, approved, rejected) | |
| moderator_comment | text, null | |
| result_object_id | uuid, null | |
| processed_by | uuid, null | |
| created_at | timestamptz | |
| processed_at | timestamptz, null | |

- `payload` - данные заявки: для `event` - `title`, `description`, `date`, `place_address`, `is_free`, `price`;
  для `club_page` - `name`, `description`, `meeting_place`, `schedule`, `contacts`
  (схемы `EventApplicationData` и `ClubPageApplicationData` в [OpenAPI](openapi.yaml)).
- `club_id` - секция-организатор, если заявка на мероприятие подана от имени секции.
- `result_object_id` - id мероприятия или секции, который Moderation назначает при одобрении
  и передаёт в `ApplicationApproved`; events-service создаёт объект с этим id.
- `moderator_comment` обязателен при `status = rejected` (CHECK `ck_applications_rejected_has_comment`).

Индексы: (`status`, `created_at`) - очередь модератора; (`author_user_id`, `created_at`) - «мои заявки».

Дубликат (#18090, п. 5.3): заявка того же автора и того же типа в статусе `on_review` с тем же
названием (`payload.title` или `payload.name`) без учёта регистра, для мероприятия - и с той же датой.
Пробелы по краям текстовых полей сервис убирает при сохранении. Дубликат не даёт создать частичный
уникальный индекс `uq_applications_on_review_duplicate` по (`author_user_id`, `type`, `lower(название)`,
`payload.date`) при `status = on_review`, в том числе при одновременной отправке двух одинаковых заявок.

## complaints

Жалобы на мероприятие или секцию (#18107, #18120, #18121).

| Поле | Тип | Ключ |
|---|---|---|
| id | uuid | PK |
| target_type | enum (event, club) | |
| target_id | uuid | |
| target_version_id | uuid | |
| author_user_id | uuid | |
| reason | enum (fraud, misleading_info, offensive, prohibited_content, spam, other) | |
| text | text, null | |
| status | enum (new, extra_check, false, true) | |
| processed_by | uuid, null | |
| moderator_comment | text, null | |
| created_at | timestamptz | |
| processed_at | timestamptz, null | |

- `reason` - обязательная причина. В ER-диаграмме поля нет, добавлено по описанию таблицы
  в вики («обязательная причина, необязательные комментарий») и требованию #18120.
- `text` - необязательный комментарий пользователя (в API - `comment`).
- `target_version_id` - версия текста на момент подачи (`event_versions.id` или `club_versions.id`),
  по ней модератор видит текст, который был в момент жалобы.
- Статусы: `new` - новая; `extra_check` - дополнительная проверка; `false` - не подтвердилась;
  `true` - подтвердилась.

Индексы: (`status`, `created_at`) - очередь модератора; (`target_type`, `target_id`) - все жалобы
на объект; (`author_user_id`, `created_at`) - «мои жалобы».

Уникальность: (`author_user_id`, `target_type`, `target_id`) при `status` in (`new`, `extra_check`) -
частичный уникальный индекс, повторная жалоба на объект до решения не создаётся (#18120, ошибка 6.2).

## moderation_log

Журнал решений модераторов (#18108). Строки только добавляются.

| Поле | Тип | Ключ |
|---|---|---|
| id | uuid | PK |
| moderator_id | uuid | |
| action_type | enum (application, complaint) | |
| object_id | uuid | |
| decision | enum (approved, rejected, confirmed, extra_check) | |
| comment | text, null | |
| created_at | timestamptz | |

Соответствие решений:

| Действие в API | `action_type` | `decision` | Новый статус объекта |
|---|---|---|---|
| заявка `approve` | application | approved | applications.status = approved |
| заявка `reject` | application | rejected | applications.status = rejected |
| жалоба `confirm` | complaint | confirmed | complaints.status = true |
| жалоба `reject` | complaint | rejected | complaints.status = false |
| жалоба `extra_check` | complaint | extra_check | complaints.status = extra_check |

Решение и запись журнала сохраняются в одной транзакции.

Индексы: (`created_at`) - журнал, новые первыми; (`moderator_id`, `created_at`); (`action_type`, `object_id`).

## Внешние связи

По идентификатору, без внешнего ключа:

- `applications.author_user_id`, `applications.processed_by`, `complaints.author_user_id`,
  `complaints.processed_by`, `moderation_log.moderator_id` → `identity_db.users.id`;
- `applications.club_id` → `events_db.clubs.id`;
- `complaints.target_id` → `events_db.events.id` или `events_db.clubs.id` (по `target_type`);
- `complaints.target_version_id` → `events_db.event_versions.id` или `events_db.club_versions.id` (по `target_type`);
- `applications.result_object_id` → созданное мероприятие или секция;
- `moderation_log.object_id` → `applications.id` или `complaints.id` (по `action_type`).

На эту базу из других баз никто не ссылается.

Таблица исходящих событий (transactional outbox) и обработанных событий - общая для всех сервисов,
описывается вместе с доставкой событий (Спринт 1).

## События брокера

Схемы - в `tusumba-contracts` (#18889).

| Событие | Когда | Получатели |
|---|---|---|
| ApplicationApproved | Модератор одобрил заявку | events-service (создаёт объект), notifications-service |
| ApplicationRejected | Модератор отклонил заявку | notifications-service |
| ComplaintResolved | Жалоба подтвердилась или не подтвердилась | notifications-service |

## API

| Запрос | Назначение | Доступ |
|---|---|---|
| POST /v1/applications | Подать заявку на мероприятие или страницу секции | Пользователь, админ секции (от имени секции) |
| GET /v1/me/applications, GET /v1/me/applications/{id} | Мои заявки и комментарий модератора | Автор |
| GET /v1/moderation/applications, GET /v1/moderation/applications/{id} | Очередь и карточка заявки | Модератор |
| POST /v1/moderation/applications/{id}/decision | Одобрить или отклонить | Модератор |
| POST /v1/complaints | Подать жалобу на мероприятие или секцию | Пользователь |
| GET /v1/me/complaints, GET /v1/me/complaints/{id} | Свои жалобы и их статус | Автор |
| GET /v1/moderation/complaints, GET /v1/moderation/complaints/{id} | Очередь и карточка жалобы с текстом на момент подачи | Модератор |
| POST /v1/moderation/complaints/{id}/decision | Подтвердить, отклонить, дополнительная проверка | Модератор |
| GET /v1/moderation/log, GET /v1/moderation/log/{id} | Журнал действий и подробности | Модератор |

## Коды ошибок

Формат ошибки: `{"error": {"code": "...", "message": "..."}}`, для `validation_error` дополнительно `details`.

| HTTP | code | Когда | Сообщение из требования |
|---|---|---|---|
| 401 | not_authenticated | Нет заголовка Authorization (tusumba-common) | |
| 401 | token_expired | Срок действия access-токена истёк (tusumba-common) | |
| 401 | invalid_token | Токен недействителен или не access (tusumba-common) | |
| 403 | forbidden | Путь модератора без роли `moderator` | |
| 403 | club_forbidden | Заявка от имени секции не её администратором | #18102: «У вас нет прав на выбранную секцию.» |
| 404 | club_not_found | Секции из `club_id` нет или она заблокирована | |
| 404 | application_not_found | Заявки нет или она чужая | |
| 404 | complaint_not_found | Жалобы нет или она чужая | #18121: «Жалоба не найдена.» |
| 404 | complaint_target_unavailable | Объекта жалобы нет или он заблокирован | #18120: «Объект жалобы недоступен.» |
| 404 | log_entry_not_found | Записи журнала нет | #18108: «Не удалось открыть детали действия.» |
| 409 | application_duplicate | Такая заявка автора уже на проверке | #18090: «У вас уже есть активная заявка на этот объект.» |
| 409 | application_already_processed | Решение по заявке уже принято | #18106: «Заявка уже обработана.» |
| 409 | complaint_duplicate | Жалоба автора на объект ещё рассматривается | #18120: «Жалоба уже отправлена.» |
| 409 | complaint_already_processed | Решение по жалобе уже принято | #18107: «Жалоба уже обработана.» |
| 422 | validation_error | Тело или параметры запроса не прошли проверку | «Заполните обязательные поля.», «Выберите причину жалобы.» |
| 503 | service_unavailable | events-service не ответил | «Не удалось отправить заявку/жалобу. Попробуйте позже.» |

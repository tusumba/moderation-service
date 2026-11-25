from tusumba_common import ApiError

REQUIRED_MESSAGE = "Заполните обязательные поля."
INVALID_DATA_MESSAGE = "Проверьте введённые данные."


def validation_error(message: str, details: list[dict[str, str]]) -> ApiError:
    return ApiError(422, "validation_error", message, details=details)


def application_duplicate() -> ApiError:
    return ApiError(409, "application_duplicate", "У вас уже есть активная заявка на этот объект.")


def club_forbidden() -> ApiError:
    return ApiError(403, "club_forbidden", "У вас нет прав на выбранную секцию.")  # #18102, 6.1


def application_not_sent() -> ApiError:
    # #18102, ошибка 6.2: events-service не ответил при проверке прав на секцию
    return ApiError(503, "service_unavailable", "Не удалось отправить заявку. Попробуйте позже.")


def application_not_found() -> ApiError:
    return ApiError(404, "application_not_found", "Заявка не найдена.")

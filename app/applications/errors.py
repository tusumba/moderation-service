from tusumba_common import ApiError

REQUIRED_MESSAGE = "Заполните обязательные поля."
INVALID_DATA_MESSAGE = "Проверьте введённые данные."


def validation_error(message: str, details: list[dict[str, str]]) -> ApiError:
    return ApiError(422, "validation_error", message, details=details)


def application_duplicate() -> ApiError:
    return ApiError(409, "application_duplicate", "У вас уже есть активная заявка на этот объект.")


def application_not_found() -> ApiError:
    return ApiError(404, "application_not_found", "Заявка не найдена.")

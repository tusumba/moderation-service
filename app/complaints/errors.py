from tusumba_common import ApiError

REASON_REQUIRED = "Выберите причину жалобы."  # #18120, ошибка 6.1


def complaint_duplicate() -> ApiError:
    return ApiError(409, "complaint_duplicate", "Жалоба уже отправлена.")  # #18120, ошибка 6.2


def complaint_not_sent() -> ApiError:
    # #18120, ошибка 6.3: events-service не ответил при проверке объекта
    return ApiError(503, "service_unavailable", "Не удалось отправить жалобу. Попробуйте позже.")


def target_unavailable() -> ApiError:
    return ApiError(404, "complaint_target_unavailable", "Объект жалобы недоступен.")  # 6.4

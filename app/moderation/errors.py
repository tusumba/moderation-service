from tusumba_common import ApiError


def application_already_processed() -> ApiError:
    # #18106, ошибка 6.1 и п. 5.2: клиент заново запрашивает карточку с актуальным статусом
    return ApiError(409, "application_already_processed", "Заявка уже обработана.")

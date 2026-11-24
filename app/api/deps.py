import uuid
from typing import Annotated

from fastapi import Depends
from tusumba_common import ApiError, CurrentUser, get_current_user, require_moderator


def _user_uuid(user: CurrentUser) -> uuid.UUID:
    """id пользователя из JWT. Identity выпускает sub как UUID, иное значение - чужой токен."""
    try:
        return uuid.UUID(user.id)
    except ValueError:
        raise ApiError(
            401, "invalid_token", "Недействительный токен", headers={"WWW-Authenticate": "Bearer"}
        ) from None


async def current_user_id(user: Annotated[CurrentUser, Depends(get_current_user)]) -> uuid.UUID:
    return _user_uuid(user)


async def moderator_id(user: Annotated[CurrentUser, Depends(require_moderator)]) -> uuid.UUID:
    """id модератора: без глобальной роли moderator - 403 forbidden (tusumba-common)."""
    return _user_uuid(user)


UserId = Annotated[uuid.UUID, Depends(current_user_id)]
ModeratorId = Annotated[uuid.UUID, Depends(moderator_id)]

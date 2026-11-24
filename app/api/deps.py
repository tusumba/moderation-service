import uuid
from typing import Annotated

from fastapi import Depends
from tusumba_common import ApiError, CurrentUser, get_current_user


async def current_user_id(user: Annotated[CurrentUser, Depends(get_current_user)]) -> uuid.UUID:
    """id пользователя из JWT. Identity выпускает sub как UUID, иное значение - чужой токен."""
    try:
        return uuid.UUID(user.id)
    except ValueError:
        raise ApiError(
            401, "invalid_token", "Недействительный токен", headers={"WWW-Authenticate": "Bearer"}
        ) from None


UserId = Annotated[uuid.UUID, Depends(current_user_id)]

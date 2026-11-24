from fastapi import APIRouter

from app.api.v1 import applications, moderation

router = APIRouter(prefix="/v1")
router.include_router(applications.router)
router.include_router(moderation.router)

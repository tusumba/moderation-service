from fastapi import APIRouter

from app.api.v1 import applications, complaints, moderation

router = APIRouter(prefix="/v1")
router.include_router(applications.router)
router.include_router(complaints.router)
router.include_router(moderation.router)

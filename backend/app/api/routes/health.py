"""健康检查"""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {"status": "ok", "app": "AI Content Growth Agent", "version": "0.5.0"}

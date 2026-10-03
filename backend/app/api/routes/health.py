"""健康检查"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app import __version__
from app.core.config import settings
from app.db.session import SessionLocal

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    from app.saas.context import is_saas_mode
    return {"status": "ok", "app": settings.APP_NAME, "version": __version__,
            "scope": "saas-single-host-pilot" if is_saas_mode() else "local-single-user"}


@router.get("/ready")
def readiness():
    try:
        # Tenant store initialization can fail before the first SQL statement.
        # Keep session creation inside this boundary, without exposing DB details.
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unavailable"})

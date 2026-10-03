"""健康检查"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    from app.saas.context import is_saas_mode
    return {"status": "ok", "app": "AI Content Growth Agent", "version": "0.8.0",
            "scope": "saas-single-host-pilot" if is_saas_mode() else "local-single-user"}


@router.get("/ready")
def readiness(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unavailable"})

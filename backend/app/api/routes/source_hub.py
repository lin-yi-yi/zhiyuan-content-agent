"""Read-only external source discovery, disabled until locally configured."""
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.services.aihot_source import AihotQuery, aihot_source, source_status


router = APIRouter(prefix="/source-hub", tags=["source-hub"])


def source_enabled():
    from app.saas.context import is_saas_mode
    if is_saas_mode():
        from app.saas.connections import connection_state
        return connection_state("aihot")["effective_enabled"]
    return settings.AIHOT_ENABLED


@router.get("/aihot/status")
def aihot_status():
    return source_status(source_enabled())


@router.get("/aihot")
async def list_aihot(response: Response, mode: str = "selected", window: str = "24h", q: str = "", category: str = "",
                     limit: int = Query(20, ge=1, le=30)):
    response.headers["Cache-Control"] = "no-store"
    try:
        query = AihotQuery(mode=mode, window=window, q=q, category=category, limit=limit)
    except ValidationError:
        raise HTTPException(422, "查询参数不正确：窗口为 24h / 7d，模式为 selected / all，关键词为 2 至 200 字，分类需在支持列表中") from None
    return await aihot_source.list_items(query, enabled=await run_in_threadpool(source_enabled))

"""Bounded public GitHub release discovery; no automatic knowledge ingestion."""
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.services.github_source import GithubQuery, github_source, source_status


router = APIRouter(prefix="/source-hub", tags=["source-hub"])


def source_enabled():
    from app.saas.context import is_saas_mode
    if is_saas_mode():
        from app.saas.connections import connection_state
        return connection_state("github")["effective_enabled"]
    return getattr(settings, "GITHUB_ENABLED", False)


@router.get("/github/status")
def github_status():
    return source_status(source_enabled())


@router.get("/github")
async def list_github(response: Response, project: str = "langchain", limit: int = Query(5, ge=1, le=20)):
    response.headers["Cache-Control"] = "no-store"
    try:
        query = GithubQuery(project=project, limit=limit)
    except ValidationError:
        raise HTTPException(422, "仅支持 langchain、dify、ragflow，条数为 1 至 20。") from None
    return await github_source.list_items(query, enabled=await run_in_threadpool(source_enabled))

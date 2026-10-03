"""Read-only approved handoff projection; organization reads use SaaS middleware."""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.delivery import build_delivery
from app.services.workflow_support import WorkflowConflict

router = APIRouter(prefix="/agent-runs", tags=["delivery"])


@router.get("/{run_id}/delivery")
def get_delivery(run_id: int, response: Response, db: Session = Depends(get_db)):
    try:
        result = build_delivery(run_id, db)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "内容任务不存在")
    response.headers["Cache-Control"] = "no-store"
    return result

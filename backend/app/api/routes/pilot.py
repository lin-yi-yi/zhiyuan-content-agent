"""Self-reported pilot outcomes; tenant auth and write roles use SaaS middleware."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.pilot import PilotRecordInput, PilotRecordOut, PilotRecordResponse, PilotReport
from app.services.pilot import build_pilot_report, get_pilot_record, save_pilot_record
from app.services.workflow_support import WorkflowConflict


router = APIRouter(prefix="/pilot", tags=["pilot"])


@router.get("/records/{run_id}", response_model=PilotRecordResponse)
def get_record(run_id: int, response: Response, db: Session = Depends(get_db)):
    result = get_pilot_record(run_id, db)
    if result is None:
        raise HTTPException(404, "内容任务不存在")
    response.headers["Cache-Control"] = "no-store"
    return result


@router.put("/records/{run_id}", response_model=PilotRecordOut)
def save_record(run_id: int, body: PilotRecordInput, response: Response, db: Session = Depends(get_db)):
    try:
        result = save_pilot_record(run_id, body, db)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "内容任务不存在")
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/report", response_model=PilotReport)
def get_report(start_date: date, end_date: date, response: Response, db: Session = Depends(get_db)):
    try:
        result = build_pilot_report(start_date, end_date, db)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return result

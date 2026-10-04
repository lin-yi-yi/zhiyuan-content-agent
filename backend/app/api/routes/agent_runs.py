"""内容增长 Agent 执行 API"""
import asyncio
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import and_, case, distinct, func, or_
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.agent_run import AgentRun, AgentStep
from app.models.model_run import ModelRun
from app.llm.tracing import serialize_model_run
from app.schemas.agent_run import AgentRunCreate, AgentRunOut, AgentRunResult, AgentReviewCreate
from app.services.content_growth_agent import (
    cancel_agent_run, create_agent_run, execute_agent_run, get_agent_run,
    prepare_retry_agent_run, review_agent_run,
    submit_agent_run_review,
)
from app.services.workflow_support import WorkflowConflict
from app.core.diagnostics import emit_diagnostic, request_diagnostic_context, safe_error_type

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


def execute_agent_run_background(run_id: int, tenant_context=None, request_id=None) -> None:
    """Starlette runs this sync function in its thread pool.

    Each worker owns its event loop and DB session. Legacy synchronous model
    clients cannot block the ASGI loop that serves polling and cancellation.
    """
    from app.saas.context import current_tenant
    token = current_tenant.set(tenant_context or current_tenant.get())
    try:
        with request_diagnostic_context(request_id):
            try:
                asyncio.run(execute_agent_run(run_id))
            except Exception as exc:
                # The 201 response may already be sent. Preserve correlation even
                # if DB/session failure prevents saving the normal task failure.
                emit_diagnostic("workflow_background_failed", agent_run_id=run_id,
                                code="BACKGROUND_FAILED", error_type=safe_error_type(exc))
    finally:
        current_tenant.reset(token)


@router.post("", response_model=AgentRunResult, status_code=201)
async def create_agent_run_endpoint(
    body: AgentRunCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """启动一次内容增长 Agent 任务。"""
    request_id = getattr(request.state, "request_id", None)
    result = create_agent_run(body, db, request_id=request_id)
    from app.saas.context import current_tenant
    background_tasks.add_task(execute_agent_run_background, result.id, current_tenant.get(), request_id)
    return result


@router.get("", response_model=list[AgentRunOut])
def list_agent_runs(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    runs = db.query(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit).all()
    return [_run_out(run, db) for run in runs]


@router.get("/{run_id}", response_model=AgentRunResult)
def get_agent_run_detail(run_id: int, db: Session = Depends(get_db)):
    result = get_agent_run(run_id, db)
    if not result:
        raise HTTPException(404, "Agent 任务不存在")
    return result


def _model_run_summary(run_id: int, db: Session) -> dict:
    """Aggregate only complete recorded usage/estimates, never partial totals.

    These are best-effort application logs, not an external-provider invoice.
    Rows without explicit run linkage are deliberately excluded.
    """
    external = and_(ModelRun.provider != "local", or_(ModelRun.call_kind != "local_rule", ModelRun.call_kind.is_(None)))
    local = or_(ModelRun.provider == "local", ModelRun.call_kind == "local_rule")
    known_usage = and_(external, ModelRun.total_tokens.is_not(None))
    priced = and_(external, ModelRun.cost_status == "estimated", ModelRun.estimated_cost.is_not(None),
                  ModelRun.estimated_cost >= 0, ModelRun.cost_currency.is_not(None), ModelRun.cost_currency != "")
    count_if = lambda predicate: func.coalesce(func.sum(case((predicate, 1), else_=0)), 0)
    row = db.query(
        func.count(ModelRun.id).label("recorded"),
        count_if(external).label("external"), count_if(local).label("local"),
        count_if(ModelRun.success.is_(False)).label("failed"), count_if(known_usage).label("known_usage"),
        func.sum(case((external, ModelRun.total_tokens), else_=None)).label("tokens"),
        count_if(priced).label("priced"),
        func.count(distinct(case((priced, ModelRun.cost_currency), else_=None))).label("currencies"),
        func.min(case((priced, ModelRun.cost_currency), else_=None)).label("currency"),
        func.sum(case((priced, ModelRun.estimated_cost), else_=None)).label("cost"),
    ).filter(ModelRun.agent_run_id == run_id).one()
    cost_status = ("no_external_requests" if not row.external else
                   "mixed_currency" if row.priced == row.external and row.currencies > 1 else
                   "complete_estimate" if row.priced == row.external and row.currencies == 1 else "incomplete")
    total_cost = Decimal(str(row.cost)) if cost_status == "complete_estimate" and row.cost is not None else None
    if total_cost is not None and not total_cost.is_finite():
        total_cost, cost_status = None, "incomplete"
    return {
        "scope": "all_recorded_rows_for_run", "recorded_call_count": row.recorded,
        "recorded_external_request_count": row.external, "local_rule_call_count": row.local,
        "failed_call_count": row.failed, "known_usage_call_count": row.known_usage,
        "unknown_usage_call_count": row.external - row.known_usage,
        "total_tokens": row.tokens if row.external and row.known_usage == row.external else None,
        "estimated_cost": format(total_cost, "f") if total_cost is not None else None,
        "cost_currency": row.currency if total_cost is not None else None,
        "cost_status": cost_status, "provider_invoice_amount": None,
        "limitation": "仅汇总明确关联任务的调用记录；日志不能代替供应商账单，未关联的历史记录不推测归属。",
    }


@router.get("/{run_id}/model-runs")
def list_agent_model_runs(run_id: int, limit: int = Query(100, ge=1, le=500),
                          offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, "Agent 任务不存在")
    summary = _model_run_summary(run_id, db)
    rows = db.query(ModelRun).filter(ModelRun.agent_run_id == run_id).order_by(
        ModelRun.created_at, ModelRun.id,
    ).offset(offset).limit(limit).all()
    return {
        "run_id": run.id, "generation_mode": ((run.result_json or {}).get("workflow") or {}).get("generation_mode", "unknown"),
        "runs": [serialize_model_run(row) for row in rows], "summary": summary,
        "total": summary["recorded_call_count"], "limit": limit, "offset": offset,
        "has_more": offset + len(rows) < summary["recorded_call_count"],
    }


@router.post("/{run_id}/retry", response_model=AgentRunResult)
async def retry_agent_run(run_id: int, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    request_id = getattr(request.state, "request_id", None)
    try:
        result = prepare_retry_agent_run(run_id, db, request_id=request_id)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Agent 任务不存在")
    from app.saas.context import current_tenant
    background_tasks.add_task(execute_agent_run_background, run_id, current_tenant.get(), request_id)
    return result


@router.post("/{run_id}/cancel", response_model=AgentRunResult)
def cancel_agent_run_endpoint(run_id: int, db: Session = Depends(get_db)):
    try:
        result = cancel_agent_run(run_id, db)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Agent 任务不存在")
    return result


@router.post("/{run_id}/review", response_model=AgentRunResult)
def review_agent_run_endpoint(run_id: int, body: AgentReviewCreate, db: Session = Depends(get_db)):
    try:
        result = review_agent_run(run_id, body, db)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Agent 任务不存在")
    return result


@router.post("/{run_id}/submit-review", response_model=AgentRunResult)
def submit_agent_run_review_endpoint(run_id: int, db: Session = Depends(get_db)):
    try:
        result = submit_agent_run_review(run_id, db)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Agent 任务不存在")
    return result


def _run_out(run: AgentRun, db: Session) -> AgentRunOut:
    steps = db.query(AgentStep).filter(AgentStep.run_id == run.id).order_by(AgentStep.step_index).all()
    return AgentRunOut(
        id=run.id,
        goal=run.goal,
        mode=run.mode,
        provider=run.provider,
        model_name=run.model_name,
        status=run.status,
        current_step=run.current_step,
        selected_topic_id=run.selected_topic_id,
        draft_id=run.draft_id,
        evaluation_score=run.evaluation_score,
        result_json=run.result_json,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
        steps=steps,
    )

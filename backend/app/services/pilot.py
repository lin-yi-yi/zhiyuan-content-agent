"""Compare explicitly reported human effort for currently accepted deliverables.

An acceptance binds the same live content/evidence gate used for delivery.
Missing observations, failed tasks and stale acceptances stay visible. These
records are self-reported evidence, not verified customer ROI or time tracking.
"""
from datetime import date, datetime, time, timedelta, timezone
from math import fsum

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent_run import AgentRun
from app.models.pilot_record import PilotRecord
from app.saas.context import current_tenant
from app.schemas.pilot import PilotRecordInput, PilotRecordOut
from app.services.delivery import build_delivery
from app.services.workflow_support import WorkflowConflict


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_pilot_record(run_id: int, db: Session) -> dict | None:
    if db.get(AgentRun, run_id) is None:
        return None
    record = db.get(PilotRecord, run_id)
    return {"run_id": run_id, "record": PilotRecordOut.model_validate(record) if record else None}


def save_pilot_record(run_id: int, body: PilotRecordInput, db: Session) -> PilotRecord | None:
    # Acquire the same run write lock used by review/edit operations before
    # checking approval. This is also safe for simultaneous first inserts.
    locked = db.execute(update(AgentRun).where(AgentRun.id == run_id).values(
        updated_at=AgentRun.updated_at).execution_options(synchronize_session=False))
    if locked.rowcount != 1:
        db.rollback()
        return None
    db.expire_all()
    existing = db.get(PilotRecord, run_id)
    if (existing is None and body.version != 0) or (existing is not None and existing.version != body.version):
        db.rollback()
        raise WorkflowConflict("试点记录已被其他操作更新，请刷新后核对再保存。")

    content_hash = None
    if body.outcome == "accepted":
        try:
            delivery = build_delivery(run_id, db)
            if delivery is None:
                raise WorkflowConflict("任务不存在，不能记录验收通过。")
            content_hash = delivery["review"]["content_hash"]
        except WorkflowConflict:
            db.rollback()
            raise
    context = current_tenant.get()
    actor = context.user_id if context else "local_user"
    now = _now()
    values = body.model_dump(exclude={"version"})
    values.update(content_hash=content_hash, updated_by=actor, updated_at=now)
    try:
        if existing is None:
            db.add(PilotRecord(run_id=run_id, version=1, source="self_reported",
                               created_by=actor, created_at=now, **values))
        else:
            changed = db.execute(update(PilotRecord).where(
                PilotRecord.run_id == run_id, PilotRecord.version == body.version,
            ).values(**values, version=PilotRecord.version + 1).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                db.rollback()
                raise WorkflowConflict("试点记录已被其他操作更新，请刷新后核对再保存。")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise WorkflowConflict("试点记录已被其他操作更新，请刷新后核对再保存。") from None
    db.expire_all()
    return db.get(PilotRecord, run_id)


def _acceptance_current(run_id: int, record: PilotRecord, db: Session) -> bool:
    try:
        delivery = build_delivery(run_id, db)
    except WorkflowConflict:
        return False
    return bool(delivery and record.content_hash and record.content_hash == delivery["review"]["content_hash"])


def build_pilot_report(start_date: date, end_date: date, db: Session) -> dict:
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    query = select(AgentRun, PilotRecord).outerjoin(PilotRecord, PilotRecord.run_id == AgentRun.id).where(
        AgentRun.created_at >= datetime.combine(start_date, time.min))
    if end_date == date.max:
        query = query.where(AgentRun.created_at <= datetime.max)
    else:
        query = query.where(AgentRun.created_at < datetime.combine(end_date + timedelta(days=1), time.min))
    rows = db.execute(query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())).all()
    counts = {key: 0 for key in ("recorded_runs", "accepted_runs", "stale_acceptances", "rejected_runs",
                                 "abandoned_runs", "pending_runs", "comparable_runs")}
    items, baselines, actuals = [], [], []
    for run, record in rows:
        current = None
        if record:
            counts["recorded_runs"] += 1
            if record.outcome == "accepted":
                current = _acceptance_current(run.id, record, db)
                counts["accepted_runs" if current else "stale_acceptances"] += 1
            else:
                counts[f"{record.outcome}_runs"] += 1
            if current and all(value is not None for value in (
                record.baseline_minutes, record.actual_work_minutes, record.support_minutes,
            )):
                counts["comparable_runs"] += 1
                baselines.append(record.baseline_minutes)
                actuals.extend((record.actual_work_minutes, record.support_minutes))
        items.append({"run_id": run.id, "goal": run.goal, "status": run.status, "created_at": run.created_at,
                      "record": PilotRecordOut.model_validate(record) if record else None,
                      "acceptance_current": current})
    baseline = fsum(baselines) if baselines else None
    actual = fsum(actuals) if baselines else None
    saved = baseline - actual if baseline is not None else None
    return {"start_date": start_date, "end_date": end_date, "total_runs": len(rows), **counts,
            "missing_records": len(rows) - counts["recorded_runs"],
            "baseline_minutes_total": baseline, "actual_minutes_total": actual,
            "saved_minutes_total": saved, "savings_rate": saved / baseline if baseline else None,
            "items": items}

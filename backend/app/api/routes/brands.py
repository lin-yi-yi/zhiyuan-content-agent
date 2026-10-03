"""Workspace-scoped brand profiles with atomic optimistic updates."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.agent_core.boundaries import workspace_context
from app.db.session import get_db
from app.models.brand_profile import BrandProfile
from app.schemas.brand_profile import BrandProfileCreate, BrandProfileOut, BrandProfileUpdate
from app.services.business_brief import _scope, workflow_templates


router = APIRouter(prefix="/brands", tags=["brands"])


def _require_write():
    # Main-app middleware checks origin, CSRF and current membership first.
    from app.saas.context import current_tenant, is_saas_mode
    if is_saas_mode():
        context = current_tenant.get()
        if context is None:
            raise HTTPException(401, "请先登录并选择有效组织")
        if context.role not in {"owner", "editor"}:
            raise HTTPException(403, "当前角色无权修改品牌档案")


@router.get("/workflows")
def list_workflows():
    return workflow_templates()


@router.get("", response_model=list[BrandProfileOut])
def list_brands(workspace_id: int | None = Query(None, ge=1), include_archived: bool = True,
                db: Session = Depends(get_db)):
    try:
        context = workspace_context(db, workspace_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    query = db.query(BrandProfile).filter(BrandProfile.workspace_id == context.workspace_id)
    if not include_archived:
        query = query.filter(BrandProfile.is_active.is_(True))
    return query.order_by(BrandProfile.updated_at.desc(), BrandProfile.id.desc()).all()


@router.post("", response_model=BrandProfileOut, status_code=201, dependencies=[Depends(_require_write)])
def create_brand(body: BrandProfileCreate, db: Session = Depends(get_db)):
    context, kb = _scope(db, body.workspace_id, body.knowledge_base_id)
    values = body.model_dump(exclude={"workspace_id", "knowledge_base_id"})
    profile = BrandProfile(**values, workspace_id=context.workspace_id, knowledge_base_id=kb.id, version=1)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.put("/{brand_id}", response_model=BrandProfileOut, dependencies=[Depends(_require_write)])
def update_brand(brand_id: int, body: BrandProfileUpdate, db: Session = Depends(get_db)):
    context, kb = _scope(db, body.workspace_id, body.knowledge_base_id)
    values = body.model_dump(exclude={"workspace_id", "knowledge_base_id", "version"})
    values.update(knowledge_base_id=kb.id, version=BrandProfile.version + 1,
                  updated_at=datetime.now(timezone.utc))
    result = db.execute(update(BrandProfile).where(
        BrandProfile.id == brand_id,
        BrandProfile.workspace_id == context.workspace_id,
        BrandProfile.version == body.version,
    ).values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        existing = db.query(BrandProfile.id).filter(
            BrandProfile.id == brand_id, BrandProfile.workspace_id == context.workspace_id,
        ).first()
        if existing is None:
            raise HTTPException(404, "品牌档案不存在或不属于当前工作区")
        raise HTTPException(409, "品牌档案已被其他操作更新，请刷新后核对再保存")
    db.commit()
    db.expire_all()
    return db.get(BrandProfile, brand_id)

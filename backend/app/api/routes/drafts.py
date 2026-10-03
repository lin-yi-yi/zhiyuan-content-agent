"""发布包草稿 API"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.draft import Draft
from app.models.review_checklist import ReviewChecklist
from app.models.topic import Topic
from app.schemas.draft import DraftOut, DraftUpdate
from app.schemas.draft_variant import DraftVariantGenerateRequest, DraftVariantResponse
from app.schemas.review_checklist import ChecklistItemOut, ChecklistUpdateRequest
from app.services.draft_variant_generator import generate_draft_variant
from app.services.review_lifecycle import has_workflow, invalidate_review_for_delete, invalidate_review_for_edit
from app.services.workflow_support import WorkflowConflict

router = APIRouter(prefix="/drafts", tags=["drafts"])


@router.get("", response_model=list[DraftOut])
def list_drafts(
    topic_id: int | None = Query(None, description="按选题筛选"),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(Draft)
    if topic_id:
        q = q.filter(Draft.topic_id == topic_id)
    drafts = q.order_by(Draft.created_at.desc()).limit(limit).all()
    return [DraftOut.model_validate(d) for d in drafts]


@router.get("/topic/{topic_id}/latest", response_model=DraftOut)
def get_latest_draft_by_topic(topic_id: int, db: Session = Depends(get_db)):
    d = db.query(Draft).filter(Draft.topic_id == topic_id).order_by(Draft.created_at.desc()).first()
    if not d:
        raise HTTPException(404, "该选题还没有发布包")
    return DraftOut.model_validate(d)


@router.get("/{draft_id}", response_model=DraftOut)
def get_draft(draft_id: int, db: Session = Depends(get_db)):
    d = db.query(Draft).filter(Draft.id == draft_id).first()
    if not d:
        raise HTTPException(404, "草稿不存在")
    return DraftOut.model_validate(d)


@router.put("/{draft_id}", response_model=DraftOut)
def update_draft(draft_id: int, body: DraftUpdate, db: Session = Depends(get_db)):
    d = db.query(Draft).filter(Draft.id == draft_id).first()
    if not d:
        raise HTTPException(404, "草稿不存在")
    values = body.model_dump(exclude_unset=True)
    from app.saas.context import current_tenant
    tenant = current_tenant.get()
    if tenant and "status" in values and tenant.role not in {"owner", "reviewer"}:
        raise HTTPException(403, "编辑者不能更改审核状态，请提交给审核者")
    linked = has_workflow(draft_id, db)
    if linked and "status" in values and values["status"] != d.status:
        raise HTTPException(409, "此草稿属于 Agent 任务，请通过任务的人工审核操作改变状态。")
    changed = any(getattr(d, key) != value for key, value in values.items() if key != "status")
    if changed:
        try:
            invalidate_review_for_edit(d, db, "草稿内容已修改，旧审核结论失效")
        except WorkflowConflict as exc:
            db.rollback()
            raise HTTPException(409, str(exc)) from exc
    for k, v in values.items():
        if k == "status" and (linked or (tenant and changed)):
            continue
        setattr(d, k, v)
    db.commit()
    db.refresh(d)
    return DraftOut.model_validate(d)


@router.delete("/{draft_id}", status_code=204)
def delete_draft(draft_id: int, db: Session = Depends(get_db)):
    d = db.query(Draft).filter(Draft.id == draft_id).first()
    if not d:
        raise HTTPException(404, "草稿不存在")
    try:
        invalidate_review_for_delete(d, db)
    except WorkflowConflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    topic_id = d.topic_id
    db.delete(d)
    db.flush()
    has_other_draft = db.query(Draft).filter(Draft.topic_id == topic_id).first()
    topic = db.query(Topic).filter(Topic.id == topic_id).first()
    if topic and topic.status == "generated" and not has_other_draft:
        topic.status = "pending"
    db.commit()


@router.post("/{draft_id}/generate-variant", response_model=DraftVariantResponse)
async def generate_variant_endpoint(draft_id: int, body: DraftVariantGenerateRequest, db: Session = Depends(get_db)):
    """根据选中的标题/封面/正文版本生成新的发布方案版本。"""
    try:
        draft, cards, variant = await generate_draft_variant(draft_id, body, db)
        return {
            "draft": DraftOut.model_validate(draft),
            "cards": cards,
            "variant": variant,
        }
    except WorkflowConflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception:
        raise HTTPException(500, "生成发布方案失败，请检查配置或稍后重试。") from None


@router.post("/{draft_id}/evaluate")
async def evaluate_draft_endpoint(draft_id: int, provider: str = "local", db: Session = Depends(get_db)):
    """发布包质量评分 — 9 维度评估"""
    from app.services.package_evaluator import evaluate_draft
    from app.models.card import Card

    d = db.query(Draft).filter(Draft.id == draft_id).first()
    if not d:
        raise HTTPException(404, "草稿不存在")
    cards = db.query(Card).filter(Card.draft_id == draft_id).order_by(Card.page_index).all()

    try:
        result = await evaluate_draft(d, cards, db, provider=provider)
        return result
    except Exception:
        raise HTTPException(500, "评分失败，请检查配置或稍后重试。") from None


# ========== 人工审核清单 ==========

DEFAULT_CHECKLIST = [
    {"key": "source_link", "label": "来源链接已保留"},
    {"key": "tool_verified", "label": "工具名称和能力已核验"},
    {"key": "no_false_claim", "label": "没有承诺固定涨粉、收入、升职、薪资"},
    {"key": "no_fake_experience", "label": "没有编造真实经历"},
    {"key": "no_sensitive_credential", "label": "没有要求用户提交账号、密码、Cookie 或敏感凭证"},
    {"key": "data_anonymized", "label": "涉及公司数据时已提醒脱敏"},
    {"key": "aigc_labeled", "label": "已加入 AIGC 标识建议"},
    {"key": "text_density_ok", "label": "卡片文字没有过密"},
    {"key": "title_not_exaggerated", "label": "封面标题没有夸大"},
    {"key": "human_reviewed", "label": "发布前已人工通读一遍"},
]


def _ensure_checklist(draft_id: int, db: Session) -> list[ReviewChecklist]:
    """确保 draft 有审核清单，没有则自动创建默认项"""
    existing = db.query(ReviewChecklist).filter(ReviewChecklist.draft_id == draft_id).all()
    if existing:
        return existing
    items = [ReviewChecklist(draft_id=draft_id, key=item["key"], label=item["label"])
             for item in DEFAULT_CHECKLIST]
    db.add_all(items)
    db.commit()
    return db.query(ReviewChecklist).filter(ReviewChecklist.draft_id == draft_id).all()


@router.get("/{draft_id}/review-checklist", response_model=list[ChecklistItemOut])
def get_checklist(draft_id: int, db: Session = Depends(get_db)):
    d = db.query(Draft).filter(Draft.id == draft_id).first()
    if not d:
        raise HTTPException(404, "草稿不存在")
    items = _ensure_checklist(draft_id, db)
    return [ChecklistItemOut.model_validate(i) for i in items]


@router.put("/{draft_id}/review-checklist", response_model=list[ChecklistItemOut])
def update_checklist(draft_id: int, body: ChecklistUpdateRequest, db: Session = Depends(get_db)):
    d = db.query(Draft).filter(Draft.id == draft_id).first()
    if not d:
        raise HTTPException(404, "草稿不存在")
    items = _ensure_checklist(draft_id, db)
    item_map = {i.key: i for i in items}
    for update in body.items:
        if update.key in item_map:
            item_map[update.key].checked = update.checked
            if update.note is not None:
                item_map[update.key].note = update.note
    db.commit()
    return [ChecklistItemOut.model_validate(i) for i in items]

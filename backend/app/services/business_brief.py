"""Resolve a business task to immutable brand and workflow configuration.

Brand settings govern expression. They never become factual RAG evidence.
The local-only policy applies to this branded content workflow, not to every
API or to an entire knowledge base.
"""
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agent_core.boundaries import get_knowledge_base_or_default, workspace_context
from app.models.brand_profile import BrandProfile
from app.schemas.brand_profile import BrandProfileOut


WORKFLOW_TEMPLATES = (
    {
        "key": "knowledge_post",
        "name": "专业知识图文",
        "description": "把自己的专业资料整理成有依据、便于理解的知识图文，审核后导出交付。",
        "required_materials": ["可用且有权使用的专业资料", "本次要解释的问题", "目标读者和期望行动"],
        "instructions": "围绕目标读者的一个真实问题，按问题、依据、要点、行动建议组织图文。每个事实只使用检索到的资料，保留引用；材料不足时指出缺口，不用品牌描述补造事实。建议使用短段落与清晰的小标题。",
    },
    {
        "key": "product_faq",
        "name": "产品与服务答疑",
        "description": "用已确认的产品资料回答常见问题，减少反复解释，形成可审核的答疑图文。",
        "required_materials": ["当前产品或服务说明", "真实常见问题", "适用条件、限制和服务边界"],
        "instructions": "整理与任务相关的常见问题，按问题、简短回答、依据或适用条件组织内容。价格、参数、服务范围及效果承诺必须有检索证据；缺失时明确待补充，不虚构优势、销量、排名或客户体验。",
    },
    {
        "key": "case_story",
        "name": "真实案例整理",
        "description": "把有权使用的案例材料整理成背景、行动和结果清晰的图文，发布前人工核对。",
        "required_materials": ["有权使用的真实案例记录", "问题背景与实际采取的行动", "有依据的结果和可公开范围"],
        "instructions": "按背景、问题、实际行动、已记录结果和适用边界整理案例。不得虚构客户身份、评价、业绩、前后对比或时间。缺少真实结果时说明尚无数据，不用想象的成功故事补全；未经明确允许不暴露个人或客户身份。",
    },
)


def workflow_templates() -> list[dict]:
    """Return copies so callers cannot modify the process-wide catalogue."""
    return deepcopy(list(WORKFLOW_TEMPLATES))


def _scope(db: Session, workspace_id: int | None, knowledge_base_id: int | None):
    try:
        context = workspace_context(db, workspace_id)
        kb = get_knowledge_base_or_default(db, context, knowledge_base_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if kb.status != "active":
        raise HTTPException(422, "请选择当前工作区中可用的知识库")
    return context, kb


def resolve_business_brief(req, db: Session) -> dict | None:
    brand_id = getattr(req, "brand_profile_id", None)
    workflow_key = getattr(req, "workflow_key", None)
    if brand_id is None and workflow_key is None:
        return None
    if not getattr(req, "use_rag", False):
        raise HTTPException(422, "品牌和固定内容流程必须使用资料依据，请开启知识库检索")

    selected_key = workflow_key or "knowledge_post"
    workflow = next((item for item in WORKFLOW_TEMPLATES if item["key"] == selected_key), None)
    if workflow is None:
        raise HTTPException(422, "内容流程不存在，请重新选择")

    workspace_id = getattr(req, "workspace_id", None)
    requested_kb = getattr(req, "knowledge_base_id", None)
    try:
        context = workspace_context(db, workspace_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    profile = None
    if brand_id is not None:
        profile = db.query(BrandProfile).filter(
            BrandProfile.id == brand_id, BrandProfile.workspace_id == context.workspace_id,
        ).first()
        if profile is None:
            raise HTTPException(404, "品牌档案不存在或不属于当前工作区")
        if not profile.is_active:
            raise HTTPException(422, "该品牌已归档，请选择可用品牌或恢复档案后再创建任务")
        if requested_kb is not None and requested_kb != profile.knowledge_base_id:
            raise HTTPException(422, "任务知识库必须与品牌绑定的知识库一致")
        if profile.data_policy == "local_only" and getattr(req, "provider", "local") != "local":
            raise HTTPException(422, "该品牌内容任务仅允许本地生成，请切换生成方式或由编辑调整品牌设置")
        requested_kb = profile.knowledge_base_id

    context, kb = _scope(db, context.workspace_id, requested_kb)
    return {
        "profile": BrandProfileOut.model_validate(profile).model_dump(mode="json") if profile else None,
        "workflow": deepcopy(workflow),
        "workspace_id": context.workspace_id,
        "knowledge_base_id": kb.id,
        "delivery": "图文交付包",
        "policy": {
            "facts_from_knowledge_only": True,
            "human_review_required": True,
            "automatic_publish": False,
        },
    }

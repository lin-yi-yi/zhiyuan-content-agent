"""素材库 API"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import StrictBool, BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.source import Source
from app.models.topic import Topic
from app.schemas.topic import TopicOut
from app.agent_core.boundaries import workspace_context
from app.agent_core.rag_service import source_index_status
from app.services.custom_topic_creator import CustomTopicIdea, ResearchReference, generate_source_topic_ideas
from app.services.source_importer import normalize_url
from app.services.topic_scorer import score_topic

import re

router = APIRouter(prefix="/sources", tags=["sources"])


class ResearchReferenceOut(BaseModel):
    title: str
    url: str = ""
    summary: str = ""
    source_type: str = "manual"
    status: str = "ok"


class SourceTopicIdeaOut(BaseModel):
    title: str
    content_angle: str
    target_audience: str
    summary: str
    reason: str
    risk_tip: str
    recommended_platform: str = "小红书图文"
    source_type: str = "source_library"
    score: int = 65
    keywords: list[str] = []
    references: list[ResearchReferenceOut] = []
    verification_status: str = "有公开来源待人工核验"
    duplicate_hint: str = ""


class SourceTopicIdeasRequest(BaseModel):
    target_audience: str = ""
    content_type: str = "auto"
    provider: str = "local"
    model: str = ""
    limit: int = Field(5, ge=3, le=5)


class SourceTopicIdeasResponse(BaseModel):
    source_id: int
    source_title: str
    research_status: str
    keywords: list[str] = []
    existing_topics: list[dict] = []
    ideas: list[SourceTopicIdeaOut] = []


class SourceTopicIdeaConfirmRequest(SourceTopicIdeaOut):
    auto_score: StrictBool = False
    provider: str = ""
    model: str = ""


@router.get("")
def list_sources(
    source_type: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(Source)
    if source_type:
        q = q.filter(Source.source_type == source_type)
    total = q.count()
    items = q.order_by(Source.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    context = workspace_context(db)

    return {
        "total": total,
        "page": page,
        "items": [{
            "id": s.id,
            "source_type": s.source_type,
            "title": s.title,
            "url": s.url,
            "summary": s.summary,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "topic_count": db.query(Topic).filter(Topic.source_id == s.id).count(),
            "quality_flags": _source_quality_flags(s, db.query(Topic).filter(Topic.source_id == s.id).count()),
            "duplicate_hint": _source_duplicate_hint(s, db),
            "rag_index": source_index_status(s.id, db, context),
        } for s in items],
    }


@router.get("/stats")
def source_stats(db: Session = Depends(get_db)):
    """素材库统计：各类型数量"""
    from sqlalchemy import func
    rows = db.query(Source.source_type, func.count(Source.id)).group_by(Source.source_type).all()
    total_sources = db.query(Source).count()
    total_topics = db.query(Topic).filter(Topic.source_id.isnot(None)).count()
    return {
        "total_sources": total_sources,
        "total_topics_from_sources": total_topics,
        "by_type": [{"source_type": r[0], "count": r[1]} for r in rows],
    }


@router.post("/{source_id}/topic-ideas", response_model=SourceTopicIdeasResponse)
async def generate_source_topic_ideas_endpoint(
    source_id: int,
    body: SourceTopicIdeasRequest = SourceTopicIdeasRequest(),
    db: Session = Depends(get_db),
):
    """从一个素材生成 3-5 个不同角度选题建议。"""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(404, "素材不存在")

    existing_topics = db.query(Topic).filter(Topic.source_id == source_id).order_by(Topic.created_at.desc()).all()
    source_summary = source.summary or source.raw_content or ""
    try:
        result = await generate_source_topic_ideas(
            source_title=source.title,
            source_summary=source_summary,
            source_url=source.url or "",
            source_type=source.source_type,
            target_audience=body.target_audience,
            content_type=body.content_type,
            provider=body.provider,
            model=body.model,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        raise HTTPException(500, "素材选题生成失败，请检查配置或稍后重试。") from None

    return SourceTopicIdeasResponse(
        source_id=source.id,
        source_title=source.title,
        research_status=result.research_status,
        keywords=result.keywords,
        existing_topics=[_topic_brief(item) for item in existing_topics],
        ideas=[
            _source_idea_out(item, existing_topics)
            for item in result.ideas[:body.limit]
        ],
    )


@router.post("/{source_id}/topic-ideas/confirm", response_model=TopicOut, status_code=201)
async def confirm_source_topic_idea(
    source_id: int,
    body: SourceTopicIdeaConfirmRequest,
    db: Session = Depends(get_db),
):
    """确认素材库里的一条候选选题并入库。"""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(404, "素材不存在")

    topic = Topic(
        source_id=source.id,
        title=body.title[:255],
        url=source.url,
        source_type=source.source_type,
        raw_summary=body.summary,
        concise_summary=body.summary[:255],
        target_audience=body.target_audience[:100] if body.target_audience else None,
        content_angle=body.content_angle[:100] if body.content_angle else None,
        recommended_platform=body.recommended_platform or "小红书图文",
        score=max(0, min(100, int(body.score or 0))),
        score_reason=_source_topic_score_reason(body),
        status="pending",
        risk_level="low",
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    if body.auto_score:
        await score_topic(topic.id, db, provider=body.provider, model=body.model or None)
        db.refresh(topic)

    return TopicOut.model_validate(topic)


@router.get("/{source_id}")
def get_source(source_id: int, db: Session = Depends(get_db)):
    s = db.query(Source).filter(Source.id == source_id).first()
    if not s:
        raise HTTPException(404, "素材不存在")
    topics = db.query(Topic).filter(Topic.source_id == source_id).order_by(Topic.created_at.desc()).all()
    context = workspace_context(db)
    return {
        "id": s.id,
        "source_type": s.source_type,
        "title": s.title,
        "url": s.url,
        "raw_content": s.raw_content,
        "summary": s.summary,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "quality_flags": _source_quality_flags(s, len(topics)),
        "duplicate_hint": _source_duplicate_hint(s, db),
        "rag_index": source_index_status(s.id, db, context),
        "topics": [{
            "id": t.id, "title": t.title, "content_angle": t.content_angle,
            "score": t.score, "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        } for t in topics],
    }


def _reference_out(item: ResearchReference) -> ResearchReferenceOut:
    return ResearchReferenceOut(
        title=item.title,
        url=item.url,
        summary=item.summary,
        source_type=item.source_type,
        status=item.status,
    )


def _source_idea_out(item: CustomTopicIdea, existing_topics: list[Topic]) -> SourceTopicIdeaOut:
    return SourceTopicIdeaOut(
        title=item.title,
        content_angle=item.content_angle,
        target_audience=item.target_audience,
        summary=item.summary,
        reason=item.reason,
        risk_tip=item.risk_tip,
        recommended_platform=item.recommended_platform,
        source_type=item.source_type,
        score=item.score,
        keywords=item.keywords,
        references=[_reference_out(ref) for ref in item.references],
        verification_status=item.verification_status,
        duplicate_hint=_duplicate_hint(item.title, existing_topics),
    )


def _source_topic_score_reason(body: SourceTopicIdeaConfirmRequest) -> str:
    parts = [
        body.reason,
        f"核验状态：{body.verification_status}" if body.verification_status else "",
        f"风险提醒：{body.risk_tip}" if body.risk_tip else "",
        f"去重提示：{body.duplicate_hint}" if body.duplicate_hint else "",
    ]
    return "\n".join(part for part in parts if part)


def _topic_brief(topic: Topic) -> dict:
    return {
        "id": topic.id,
        "title": topic.title,
        "content_angle": topic.content_angle,
        "score": topic.score,
        "status": topic.status,
    }


def _duplicate_hint(title: str, existing_topics: list[Topic]) -> str:
    normalized = _normalize_title(title)
    if not normalized:
        return ""
    for topic in existing_topics:
        candidate = _normalize_title(topic.title)
        if candidate == normalized:
            return f"同素材下已有相同标题：#{topic.id}"
        if _title_similarity(normalized, candidate) >= 0.72:
            return f"可能与已有选题 #{topic.id}「{topic.title[:28]}」重复"
    return ""


def _normalize_title(value: str) -> str:
    return re.sub(r"[\W_]+", "", (value or "").lower())


def _title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    return len(left_set & right_set) / max(1, len(left_set | right_set))


def _source_quality_flags(source: Source, topic_count: int) -> list[str]:
    flags: list[str] = []
    summary = source.summary or ""
    if not source.url:
        flags.append("无来源链接")
    if len(summary) < 80:
        flags.append("摘要偏短")
    if topic_count == 0:
        flags.append("尚未拆题")
    if source.source_type in {"custom_idea", "custom_research"} and not source.url:
        flags.append("观点/调研需人工核验")
    if not flags:
        flags.append("可复用")
    return flags[:4]


def _source_duplicate_hint(source: Source, db: Session) -> str:
    if source.url:
        try:
            normalized_url = normalize_url(source.url)
        except Exception:
            normalized_url = source.url
        existing = (
            db.query(Source)
            .filter(Source.id != source.id, Source.url == normalized_url)
            .order_by(Source.created_at.desc())
            .first()
        )
        if existing:
            return f"URL 与素材 #{existing.id} 相同"

    title_key = _normalize_title(source.title)
    summary_key = _normalize_title((source.summary or "")[:120])
    candidates = db.query(Source).filter(Source.id != source.id).order_by(Source.created_at.desc()).limit(200).all()
    for candidate in candidates:
        title_score = _title_similarity(title_key, _normalize_title(candidate.title))
        summary_score = _title_similarity(summary_key, _normalize_title((candidate.summary or "")[:120]))
        if title_score >= 0.82:
            return f"标题可能与素材 #{candidate.id}「{candidate.title[:28]}」相近"
        if summary_key and summary_score >= 0.88:
            return f"摘要可能与素材 #{candidate.id}「{candidate.title[:28]}」相近"
    return ""

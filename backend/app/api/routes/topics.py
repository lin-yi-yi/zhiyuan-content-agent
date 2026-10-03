"""选题池 CRUD + 评分"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import StrictBool, BaseModel
from sqlalchemy.orm import Session

import re

from app.db.session import get_db
from app.models.source import Source
from app.models.topic import Topic
from app.schemas.topic import TopicCreate, TopicListOut, TopicOut, TopicUpdate
from app.services.custom_topic_creator import CustomTopicIdea, ResearchReference, generate_custom_topic_ideas
from app.services.source_importer import ImportedSource, TopicSuggestion, generate_topic_suggestions, import_source_from_url, normalize_url
from app.services.topic_scorer import score_topic

router = APIRouter(prefix="/topics", tags=["topics"])


class ScoreRequest(BaseModel):
    provider: str = ""
    model: str = ""


class ImportUrlRequest(BaseModel):
    url: str
    source_type: str = ""
    fallback_summary: str = ""
    auto_score: StrictBool = False
    provider: str = ""
    model: str = ""


class TopicSuggestionOut(BaseModel):
    title: str
    content_angle: str
    target_audience: str
    summary: str
    reason: str
    risk_tip: str


class ImportUrlPreviewOut(BaseModel):
    source_type: str
    title: str
    url: str
    raw_content: str
    summary: str
    topic_title: str
    suggestions: list[TopicSuggestionOut] = []


class ImportUrlConfirmRequest(BaseModel):
    url: str
    source_type: str
    title: str
    raw_content: str = ""
    summary: str
    topic_title: str
    content_angle: str = ""
    target_audience: str = ""
    suggestion_reason: str = ""
    risk_tip: str = ""
    auto_score: StrictBool = False
    provider: str = ""
    model: str = ""


class ResearchReferenceOut(BaseModel):
    title: str
    url: str = ""
    summary: str = ""
    source_type: str = "manual"
    status: str = "ok"


class CustomTopicIdeaOut(BaseModel):
    title: str
    content_angle: str
    target_audience: str
    summary: str
    reason: str
    risk_tip: str
    recommended_platform: str = "小红书图文"
    source_type: str = "custom_idea"
    score: int = 65
    keywords: list[str] = []
    references: list[ResearchReferenceOut] = []
    verification_status: str = "未核验"
    duplicate_hint: str = ""


class CustomTopicIdeasRequest(BaseModel):
    mode: str = "inspiration"
    research_depth: str = "quick"
    theme: str
    target_audience: str = ""
    viewpoint: str = ""
    personal_case: str = ""
    content_type: str = "auto"
    source_urls: list[str] = []
    provider: str = "local"
    model: str = ""


class CustomTopicIdeasResponse(BaseModel):
    mode: str
    research_depth: str
    research_status: str
    keywords: list[str] = []
    references: list[ResearchReferenceOut] = []
    ideas: list[CustomTopicIdeaOut] = []


class CustomTopicConfirmRequest(CustomTopicIdeaOut):
    auto_score: StrictBool = False
    provider: str = ""
    model: str = ""


@router.get("", response_model=TopicListOut)
async def list_topics(
    status: str = Query(None, description="按状态筛选"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(Topic)
    if status:
        q = q.filter(Topic.status == status)
    total = q.count()
    items = q.order_by(Topic.score.desc(), Topic.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return TopicListOut(total=total, items=[TopicOut.model_validate(t) for t in items])


@router.post("/import-url/preview", response_model=ImportUrlPreviewOut)
async def preview_import_url(body: ImportUrlRequest):
    try:
        imported = await import_source_from_url(
            body.url,
            source_type=body.source_type,
            fallback_summary=body.fallback_summary,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"预览失败: {str(e)}")
    return ImportUrlPreviewOut(
        source_type=imported.source_type,
        title=imported.title,
        url=imported.url,
        raw_content=imported.raw_content[:20000],
        summary=imported.summary,
        topic_title=imported.topic_title,
        suggestions=[
            TopicSuggestionOut(
                title=item.title,
                content_angle=item.content_angle,
                target_audience=item.target_audience,
                summary=item.summary,
                reason=item.reason,
                risk_tip=item.risk_tip,
            )
            for item in generate_topic_suggestions(imported)
        ],
    )


@router.post("/import-url/confirm", response_model=TopicOut, status_code=201)
async def confirm_import_url(body: ImportUrlConfirmRequest, db: Session = Depends(get_db)):
    try:
        imported = ImportedSource(
            source_type=body.source_type or "other",
            title=body.title,
            url=normalize_url(body.url),
            raw_content=body.raw_content or body.summary,
            summary=body.summary,
            topic_title=body.topic_title,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"确认导入失败: {str(e)}")
    suggestion = TopicSuggestion(
        title=body.topic_title,
        content_angle=body.content_angle,
        target_audience=body.target_audience,
        summary=body.summary,
        reason=body.suggestion_reason,
        risk_tip=body.risk_tip,
    )
    return await _create_imported_topic(imported, db, body.auto_score, body.provider, body.model, suggestion)


@router.post("/import-url", response_model=TopicOut, status_code=201)
async def import_url(body: ImportUrlRequest, db: Session = Depends(get_db)):
    try:
        imported = await import_source_from_url(
            body.url,
            source_type=body.source_type,
            fallback_summary=body.fallback_summary,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"导入失败: {str(e)}")

    suggestions = generate_topic_suggestions(imported)
    suggestion = suggestions[0] if suggestions else None
    return await _create_imported_topic(imported, db, body.auto_score, body.provider, body.model, suggestion)


@router.post("/custom-ideas", response_model=CustomTopicIdeasResponse)
async def custom_topic_ideas(body: CustomTopicIdeasRequest, db: Session = Depends(get_db)):
    """自定义选题创作：调研型 / 灵感型选题建议。"""
    try:
        result = await generate_custom_topic_ideas(
            mode=body.mode,
            research_depth=body.research_depth,
            theme=body.theme,
            target_audience=body.target_audience,
            viewpoint=body.viewpoint,
            personal_case=body.personal_case,
            content_type=body.content_type,
            source_urls=body.source_urls,
            provider=body.provider,
            model=body.model,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="自定义选题生成失败，请检查配置或稍后重试。") from None

    return CustomTopicIdeasResponse(
        mode=result.mode,
        research_depth=result.research_depth,
        research_status=result.research_status,
        keywords=result.keywords,
        references=[_reference_out(item) for item in result.references],
        ideas=[_idea_out(item, db) for item in result.ideas],
    )


@router.post("/custom-ideas/confirm", response_model=TopicOut, status_code=201)
async def confirm_custom_topic_idea(body: CustomTopicConfirmRequest, db: Session = Depends(get_db)):
    """确认一个自定义选题建议并写入选题池。"""
    references = [
        ResearchReference(
            title=item.title,
            url=item.url,
            summary=item.summary,
            source_type=item.source_type,
            status=item.status,
        )
        for item in body.references
    ]
    source_type = body.source_type or "custom_idea"
    first_url = next((item.url for item in references if item.url and item.status == "ok"), None)
    raw_content = _custom_source_raw(body, references)

    source = Source(
        source_type=source_type,
        title=body.title[:255],
        url=first_url,
        raw_content=raw_content[:20000],
        summary=body.summary,
    )
    db.add(source)
    db.flush()

    topic = Topic(
        source_id=source.id,
        title=body.title[:255],
        url=first_url,
        source_type=source_type,
        raw_summary=body.summary,
        concise_summary=body.summary[:255],
        target_audience=body.target_audience[:100] if body.target_audience else None,
        content_angle=body.content_angle[:100] if body.content_angle else None,
        recommended_platform=body.recommended_platform or "小红书图文",
        score=max(0, min(100, int(body.score or 0))),
        score_reason=_custom_score_reason(body),
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


async def _create_imported_topic(
    imported: ImportedSource,
    db: Session,
    auto_score: StrictBool = False,
    provider: str = "",
    model: str = "",
    suggestion: TopicSuggestion | None = None,
) -> TopicOut:
    existing = db.query(Topic).filter(Topic.url == imported.url).order_by(Topic.created_at.desc()).first()
    if existing:
        return TopicOut.model_validate(existing)

    source = Source(
        source_type=imported.source_type,
        title=imported.title[:255],
        url=imported.url,
        raw_content=imported.raw_content[:20000],
        summary=imported.summary,
    )
    db.add(source)
    db.flush()

    topic = Topic(
        source_id=source.id,
        title=(suggestion.title if suggestion and suggestion.title else imported.topic_title)[:255],
        url=imported.url,
        source_type=imported.source_type,
        raw_summary=(suggestion.summary if suggestion and suggestion.summary else imported.summary),
        target_audience=suggestion.target_audience if suggestion and suggestion.target_audience else None,
        content_angle=suggestion.content_angle if suggestion and suggestion.content_angle else None,
        score_reason=_suggestion_reason(suggestion),
        status="pending",
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    if auto_score:
        await score_topic(topic.id, db, provider=provider, model=model or None)
        db.refresh(topic)

    return TopicOut.model_validate(topic)


def _suggestion_reason(suggestion: TopicSuggestion | None) -> str | None:
    if not suggestion:
        return None
    parts = [part for part in [suggestion.reason, f"风险提醒：{suggestion.risk_tip}" if suggestion.risk_tip else ""] if part]
    return "\n".join(parts) if parts else None


def _reference_out(item: ResearchReference) -> ResearchReferenceOut:
    return ResearchReferenceOut(
        title=item.title,
        url=item.url,
        summary=item.summary,
        source_type=item.source_type,
        status=item.status,
    )


def _idea_out(item: CustomTopicIdea, db: Session | None = None) -> CustomTopicIdeaOut:
    return CustomTopicIdeaOut(
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
        duplicate_hint=_custom_duplicate_hint(item, db) if db else "",
    )


def _custom_score_reason(body: CustomTopicConfirmRequest) -> str:
    parts = [
        body.reason,
        f"核验状态：{body.verification_status}" if body.verification_status else "",
        f"风险提醒：{body.risk_tip}" if body.risk_tip else "",
        f"去重提示：{body.duplicate_hint}" if body.duplicate_hint else "",
    ]
    return "\n".join(part for part in parts if part)


def _custom_source_raw(body: CustomTopicConfirmRequest, references: list[ResearchReference]) -> str:
    refs = "\n".join(
        f"- {item.title} | {item.url or '无链接'} | {item.summary}"
        for item in references
    )
    keywords = "、".join(body.keywords or [])
    return (
        f"自定义选题：{body.title}\n"
        f"角度：{body.content_angle}\n"
        f"目标人群：{body.target_audience}\n"
        f"核验状态：{body.verification_status}\n"
        f"关键词：{keywords}\n\n"
        f"摘要：{body.summary}\n\n"
        f"为什么值得写：{body.reason}\n"
        f"风险提醒：{body.risk_tip}\n\n"
        f"参考来源：\n{refs or '无'}"
    )


def _custom_duplicate_hint(idea: CustomTopicIdea, db: Session) -> str:
    ok_urls = []
    for ref in idea.references:
        if ref.url and ref.status == "ok":
            try:
                ok_urls.append(normalize_url(ref.url))
            except Exception:
                ok_urls.append(ref.url)
    for url in ok_urls:
        existing_source = db.query(Source).filter(Source.url == url).order_by(Source.created_at.desc()).first()
        if existing_source:
            return f"来源链接已在素材库存在：#{existing_source.id}「{existing_source.title[:28]}」"
        existing_topic = db.query(Topic).filter(Topic.url == url).order_by(Topic.created_at.desc()).first()
        if existing_topic:
            return f"来源链接已有关联选题：#{existing_topic.id}「{existing_topic.title[:28]}」"

    normalized_title = _normalize_text_key(idea.title)
    if normalized_title:
        recent_topics = db.query(Topic).order_by(Topic.created_at.desc()).limit(200).all()
        for topic in recent_topics:
            candidate = _normalize_text_key(topic.title)
            if candidate == normalized_title:
                return f"已有相同选题：#{topic.id}"
            if _text_similarity(normalized_title, candidate) >= 0.74:
                return f"可能与已有选题 #{topic.id}「{topic.title[:28]}」重复"

    normalized_summary = _normalize_text_key(idea.summary[:120])
    recent_sources = db.query(Source).order_by(Source.created_at.desc()).limit(200).all()
    for source in recent_sources:
        title_score = _text_similarity(normalized_title, _normalize_text_key(source.title))
        summary_score = _text_similarity(normalized_summary, _normalize_text_key((source.summary or "")[:120]))
        if title_score >= 0.76 or (normalized_summary and summary_score >= 0.82):
            return f"可能与已有素材 #{source.id}「{source.title[:28]}」相近"
    return ""


def _normalize_text_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", (value or "").lower())


def _text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    return len(left_set & right_set) / max(1, len(left_set | right_set))


@router.post("", response_model=TopicOut, status_code=201)
async def create_topic(body: TopicCreate, db: Session = Depends(get_db)):
    topic = Topic(**body.model_dump())
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return TopicOut.model_validate(topic)


@router.get("/{topic_id}", response_model=TopicOut)
async def get_topic(topic_id: int, db: Session = Depends(get_db)):
    topic = db.query(Topic).filter(Topic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="选题不存在")
    return TopicOut.model_validate(topic)


@router.put("/{topic_id}", response_model=TopicOut)
async def update_topic(topic_id: int, body: TopicUpdate, db: Session = Depends(get_db)):
    topic = db.query(Topic).filter(Topic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="选题不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(topic, k, v)
    db.commit()
    db.refresh(topic)
    return TopicOut.model_validate(topic)


@router.delete("/{topic_id}", status_code=204)
async def delete_topic(topic_id: int, db: Session = Depends(get_db)):
    topic = db.query(Topic).filter(Topic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="选题不存在")
    db.delete(topic)
    db.commit()


@router.post("/{topic_id}/score")
async def score_topic_endpoint(topic_id: int, req: ScoreRequest = ScoreRequest(), db: Session = Depends(get_db)):
    """AI 评分选题 — 7 维度打分，更新 topics 表"""
    try:
        result = await score_topic(topic_id, db, provider=req.provider, model=req.model or None)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="评分失败，请检查配置或稍后重试。") from None


@router.post("/{topic_id}/generate-draft")
async def generate_draft_endpoint(topic_id: int, req: ScoreRequest = ScoreRequest(), db: Session = Depends(get_db)):
    """一键生成发布包：DraftAgent → CardAgent → ComplianceAgent"""
    from app.services.draft_generator import generate_draft
    from app.services.card_generator import generate_cards
    from app.services.compliance_checker import check_compliance
    from app.schemas.draft import DraftOut
    from app.schemas.card import CardOut

    topic = db.query(Topic).filter(Topic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="选题不存在")

    try:
        try:
            draft = await generate_draft(topic, db, provider=req.provider, model=req.model or None)
        except Exception:
            raise HTTPException(500, "发布包生成失败，请检查配置或稍后重试。") from None

        try:
            cards = await generate_cards(draft, db, provider=req.provider, model=req.model or None)
        except Exception:
            raise HTTPException(500, "卡片生成失败，请检查配置或稍后重试。") from None

        try:
            await check_compliance(draft, db, provider=req.provider, model=req.model or None)
        except Exception:
            raise HTTPException(500, "合规检查失败，请检查配置或稍后重试。") from None

        topic.status = "generated"
        db.commit()

        return {
            "draft": DraftOut.model_validate(draft).model_dump(),
            "cards": [CardOut.model_validate(c).model_dump() for c in cards],
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="发布包处理失败，请稍后重试。") from None

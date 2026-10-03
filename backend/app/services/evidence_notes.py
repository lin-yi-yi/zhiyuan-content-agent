"""Explicit user review before immutable notes may become retrieval evidence.

No endpoint fetches a citation URL or copies provider summaries. Mutations share
the local index lock; the application remains a single-process personal tool.
"""
from datetime import datetime, timezone
import hashlib
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agent_core.boundaries import get_knowledge_base_or_default, workspace_context
from app.agent_core.rag_service import INDEX_LOCK, MAX_DOCUMENT_CHARS, index_source
from app.models.evidence_note import (
    EvidenceNote, evidence_note_is_current, structured_fact_conflicts,
    structured_fact_errors, utc_datetime,
)
from app.models.knowledge_base import KnowledgeDocument
from app.models.source import Source
from app.services.safe_fetch import normalize_public_url


class EvidenceError(ValueError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def _reference_url(value):
    value = value.strip()
    if not value.lower().startswith(("https://", "http://")):
        raise ValueError("引用必须为完整 HTTP/HTTPS 公网链接，不会自动抓取")
    normalized = urlsplit(normalize_public_url(value))
    # A citation locator is meaningful evidence, even though fetch URLs omit it.
    return urlunsplit((*normalized[:4], urlsplit(value).fragment))


class NoteScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: int | None = Field(default=None, ge=1)
    knowledge_base_id: int | None = Field(default=None, ge=1)


class NoteCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    claim: str = Field(min_length=1, max_length=1000)
    excerpt: str = Field(min_length=1, max_length=3000)
    source_url: str = Field(min_length=1, max_length=2000)
    locator: str = Field(default="", max_length=500)
    product_model: str = Field(default="", max_length=200)
    parameter: str = Field(default="", max_length=200)
    value: str = Field(default="", max_length=500)

    _safe_url = field_validator("source_url")(_reference_url)


class NoteCreate(NoteScope):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=40, max_length=MAX_DOCUMENT_CHARS)
    source_url: str = Field(min_length=1, max_length=2000)
    provider: Literal["manual", "github", "aihot"] = "manual"
    version_label: str = Field(default="", max_length=200)
    published_at: datetime | None = None
    expires_at: datetime | None = None
    citations: list[NoteCitation] = Field(default_factory=list, max_length=30)
    rights_basis: Literal["own", "permission", "reference_only"] = "reference_only"
    rights_note: str = Field(default="", max_length=3000)
    own_note_confirmed: bool = Field(default=False, strict=True)

    _safe_url = field_validator("source_url")(_reference_url)

    @field_validator("published_at", "expires_at")
    @classmethod
    def normalize_time(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("时间必须包含时区，例如 +08:00 或 Z")
        return utc_datetime(value)

    @model_validator(mode="after")
    def own_note_and_dates(self):
        if self.provider == "aihot" and not self.own_note_confirmed:
            raise ValueError("AIHOT 只用于发现链接；请确认正文是本人核验笔记，而非自动复制 AI 摘要")
        if self.published_at and self.expires_at and self.expires_at <= self.published_at:
            raise ValueError("失效时间必须晚于来源发布时间")
        errors = structured_fact_errors(self.content, self.version_label,
                                        [citation.model_dump() for citation in self.citations])
        if errors:
            raise ValueError("；".join(errors))
        return self


class NoteReview(NoteScope):
    decision: Literal["verify", "reject", "revoke"]
    confirmed_sources: bool = Field(default=False, strict=True)
    note: str = Field(default="", max_length=2000)


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    value = utc_datetime(value)
    return value.isoformat() if value else None


def _expired(note):
    return note.expires_at is not None and utc_datetime(note.expires_at) <= _now()


def _scope(db, scope):
    try:
        context = workspace_context(db, scope.workspace_id)
        kb = get_knowledge_base_or_default(db, context, scope.knowledge_base_id)
        return context, kb
    except ValueError:
        raise EvidenceError("工作区或知识库不存在，或不属于当前范围", 404) from None


def _note(db, note_id, scope):
    context, kb = _scope(db, scope)
    note = db.query(EvidenceNote).filter(EvidenceNote.id == note_id,
        EvidenceNote.workspace_id == context.workspace_id, EvidenceNote.knowledge_base_id == kb.id).first()
    if note is None:
        raise EvidenceError("证据笔记不存在或不属于当前知识库", 404)
    return note, context, kb


def _scope_notes(db, note):
    return db.query(EvidenceNote).filter(EvidenceNote.workspace_id == note.workspace_id,
        EvidenceNote.knowledge_base_id == note.knowledge_base_id).all()


def note_output(note, peers=()):
    expired = _expired(note)
    conflicts = structured_fact_conflicts(peers, candidate_id=note.id if note.review_status == "pending" else None).get(note.id, [])
    usable = evidence_note_is_current(note) and not conflicts
    return {"id": note.id, "workspace_id": note.workspace_id, "knowledge_base_id": note.knowledge_base_id,
        "title": note.title, "content": note.content, "content_hash": note.content_hash,
        "source_url": note.source_url, "provider": note.provider, "version_label": note.version_label,
        "published_at": _iso(note.published_at), "expires_at": _iso(note.expires_at),
        "citations": note.citations, "rights_basis": note.rights_basis, "rights_note": note.rights_note,
        "own_note_confirmed": note.own_note_confirmed, "review_status": note.review_status,
        "review_note": note.review_note, "reviewed_at": _iso(note.reviewed_at),
        "verified_at": _iso(note.verified_at), "review_history": note.review_history,
        "source_id": note.source_id, "document_id": note.document_id,
        "index_status": "indexed" if usable else ("stale" if note.document_id else "not_indexed"),
        "index_state": note.index_state, "index_error": note.index_error, "indexed_at": _iso(note.indexed_at),
        "is_expired": expired, "indexable": note.review_status == "verified" and not expired and not conflicts,
        "fact_conflict_note_ids": conflicts,
        "created_at": _iso(note.created_at), "updated_at": _iso(note.updated_at)}


def evidence_metadata(note):
    return {"note_id": note.id, "status": note.review_status, "verified_at": _iso(note.verified_at),
        "expires_at": _iso(note.expires_at), "version_label": note.version_label,
        "citations": note.citations, "source_url": note.source_url, "provider": note.provider,
        "rights_basis": note.rights_basis, "published_at": _iso(note.published_at),
        "content_hash": note.content_hash}


def _documents(db, note):
    filters = []
    if note.source_id:
        filters.append(KnowledgeDocument.source_id == note.source_id)
    if note.document_id:
        filters.append(KnowledgeDocument.id == note.document_id)
    return db.query(KnowledgeDocument).filter(or_(*filters)).all() if filters else []


def create_note(body: NoteCreate, db: Session):
    with INDEX_LOCK:
        context, kb = _scope(db, body)
        fields = body.model_dump(exclude={"workspace_id", "knowledge_base_id"})
        note = EvidenceNote(**fields, workspace_id=context.workspace_id, knowledge_base_id=kb.id,
            content_hash=hashlib.sha256(body.content.encode()).hexdigest(), review_status="pending",
            index_state="not_indexed", review_history=[])
        db.add(note)
        db.commit()
        db.refresh(note)
        return note_output(note, _scope_notes(db, note))


def list_notes(db: Session, scope: NoteScope, status=None, limit=50, offset=0):
    context, kb = _scope(db, scope)
    query = db.query(EvidenceNote).filter(EvidenceNote.workspace_id == context.workspace_id,
                                         EvidenceNote.knowledge_base_id == kb.id)
    if status:
        query = query.filter(EvidenceNote.review_status == status)
    peers = db.query(EvidenceNote).filter(EvidenceNote.workspace_id == context.workspace_id,
        EvidenceNote.knowledge_base_id == kb.id).all()
    return {"items": [note_output(note, peers) for note in query.order_by(EvidenceNote.created_at.desc(),
        EvidenceNote.id.desc()).offset(offset).limit(limit)], "total": query.count()}


def get_note(note_id: int, scope: NoteScope, db: Session):
    note, _, _ = _note(db, note_id, scope)
    return note_output(note, _scope_notes(db, note))


def _review_requirements(note, db):
    if note.rights_basis not in {"own", "permission"}:
        raise EvidenceError("仅链接参考的内容不能核验入库；需确认自有内容或已有授权", 409)
    if note.rights_basis == "permission" and not note.rights_note.strip():
        raise EvidenceError("授权内容需要填写授权依据说明", 409)
    if not note.citations:
        raise EvidenceError("至少需要一条包含结论、摘录及来源链接的核验引用", 409)
    try:
        for citation in note.citations:
            NoteCitation.model_validate(citation)
    except (ValidationError, TypeError):
        raise EvidenceError("核验引用不完整，请新建包含结论、摘录及安全来源链接的修订版", 409) from None
    if _expired(note):
        raise EvidenceError("笔记已经过期，请新建核验后的修订版", 409)
    if note.provider == "aihot" and not note.own_note_confirmed:
        raise EvidenceError("AIHOT 来源仅可保存本人核验笔记", 409)
    errors = structured_fact_errors(note.content, note.version_label, note.citations)
    if errors:
        raise EvidenceError("；".join(errors), 409)
    conflicts = structured_fact_conflicts(_scope_notes(db, note), candidate_id=note.id).get(note.id, [])
    if conflicts:
        ids = "、".join(f"#{note_id}" for note_id in conflicts)
        raise EvidenceError(f"结构化事实冲突：笔记 {ids} 对同一品牌及产品型号的相同参数登记了不同值；请核对来源、适用条件，撤销旧版或新建修订后重新核验。", 409)


def review_note(note_id: int, body: NoteReview, db: Session):
    with INDEX_LOCK:
        db.expire_all()
        note, context, _ = _note(db, note_id, body)
        if body.decision == "verify":
            if not body.confirmed_sources:
                raise EvidenceError("需要人工明确确认已核对引用来源", 409)
            _review_requirements(note, db)
        now = _now()
        note.review_status = {"verify": "verified", "reject": "rejected", "revoke": "revoked"}[body.decision]
        note.review_note, note.reviewed_at = body.note.strip(), now
        if body.decision == "verify":
            note.verified_at = now
        note.review_history = [*(note.review_history or []), {"decision": body.decision,
            "review_status": note.review_status, "confirmed_sources": body.confirmed_sources,
            "note": note.review_note, "reviewed_at": _iso(now), "actor": context.actor}]
        note.index_state, note.index_error = ("stale" if note.document_id else "not_indexed"), None
        for document in _documents(db, note):
            document.status = "disabled"
            document.metadata_json = {**(document.metadata_json or {}), "evidence": evidence_metadata(note)}
        db.commit()
        db.refresh(note)
        return note_output(note, _scope_notes(db, note))


def index_note(note_id: int, body: NoteScope, db: Session):
    with INDEX_LOCK:
        db.expire_all()
        note, context, kb = _note(db, note_id, body)
        if note.review_status != "verified":
            raise EvidenceError("仅人工核验通过的笔记可以入库", 409)
        _review_requirements(note, db)
        # The immutable note is authoritative, including its original content hash.
        if hashlib.sha256(note.content.encode()).hexdigest() != note.content_hash:
            raise EvidenceError("核验内容已经改变，请新建修订版重新核验", 409)
        source = db.get(Source, note.source_id) if note.source_id else None
        if source is None:
            source = Source(source_type="evidence_note", title=note.title, url=note.source_url,
                            raw_content=note.content, summary=note.content[:220])
            db.add(source)
            db.flush()
            note.source_id = source.id
        else:
            source.source_type, source.title, source.url = "evidence_note", note.title, note.source_url
            source.raw_content, source.summary = note.content, note.content[:220]
        note.index_state, note.index_error = "indexing", None
        db.commit()
        try:
            result = index_source(source.id, db, context, kb.id)
            document = db.get(KnowledgeDocument, result["document_id"])
            if (document is None or document.workspace_id != note.workspace_id
                    or document.knowledge_base_id != note.knowledge_base_id or document.source_id != note.source_id):
                raise RuntimeError("Unexpected index scope")
            db.refresh(note)
            _review_requirements(note, db)  # Expiry may pass while embeddings are being computed.
            if note.review_status != "verified":
                raise EvidenceError("核验状态已改变，无法完成入库", 409)
            note.document_id = document.id
            note.index_state, note.indexed_at, note.index_error = "indexed", _now(), None
            document.metadata_json = {**(document.metadata_json or {}), "evidence": evidence_metadata(note)}
            document.status = "indexed"
            db.commit()
            db.refresh(note)
            return {"note": note_output(note, _scope_notes(db, note)), "document_id": document.id,
                    "deduplicated": bool(result.get("deduplicated")), "index": result}
        except Exception:
            db.rollback()
            note = db.get(EvidenceNote, note_id, populate_existing=True)
            note.index_state, note.index_error = "error", "索引未完成；请检查检索配置后重试"
            for document in _documents(db, note):
                document.status = "disabled"
                document.metadata_json = {**(document.metadata_json or {}), "evidence": evidence_metadata(note)}
            db.commit()
            raise EvidenceError("索引未完成，未将笔记标为已入库；请检查检索配置后重试", 503) from None

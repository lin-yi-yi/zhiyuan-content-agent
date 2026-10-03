"""Local knowledge workbench: bounded text uploads, index lifecycle and evaluation."""
import hashlib
import json
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.agent_core.boundaries import get_knowledge_base_or_default, workspace_context
from app.agent_core.embeddings import RetrievalError, retrieval_config, retrieval_status
from app.agent_core.rag_service import INDEX_LOCK, MAX_DOCUMENT_CHARS, answer_question, index_source
from app.agent_core import vector_store
from app.agent_core.evidence_policy import document_evidence_states, evidence_health
from app.db.session import get_db
from app.models.knowledge_base import KnowledgeChunk, KnowledgeDocument
from app.models.source import Source
from app.models.evidence_note import EvidenceNote
from app.services.document_parser import MAX_ENCODED_LENGTH, DocumentParseError, parse_document

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class DocumentUpload(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=40, max_length=MAX_DOCUMENT_CHARS)
    source_uri: str = Field(default="", max_length=2000)
    format: str = Field(default="markdown", pattern="^(markdown|text)$")
    document_id: int | None = Field(default=None, ge=1)
    workspace_id: int | None = Field(default=None, ge=1)
    knowledge_base_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_content(self):
        self.title, self.content, self.source_uri = self.title.strip(), self.content.strip(), self.source_uri.strip()
        if not self.title or len(self.content) < 40:
            raise ValueError("标题不能为空，正文至少 40 个字符")
        if self.source_uri and urlparse(self.source_uri).scheme not in {"http", "https", "obsidian", "demo"}:
            raise ValueError("来源仅支持 http、https、obsidian 或 demo 引用地址；不会抓取该地址")
        return self


class DocumentImportPreview(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=MAX_ENCODED_LENGTH)
    workspace_id: int | None = Field(default=None, ge=1)
    knowledge_base_id: int | None = Field(default=None, ge=1)


class EvaluationCase(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    expected_document_ids: list[int] = Field(default_factory=list, max_length=30)
    expected_chunk_ids: list[int] = Field(default_factory=list, max_length=30)
    should_refuse: bool = False

    @model_validator(mode="after")
    def validate_labels(self):
        if self.should_refuse and (self.expected_document_ids or self.expected_chunk_ids):
            raise ValueError("拒答样本不能同时标注相关证据")
        if not self.should_refuse and not (self.expected_document_ids or self.expected_chunk_ids):
            raise ValueError("可回答样本必须标注相关文档或知识块")
        return self


class EvaluationRequest(BaseModel):
    cases: list[EvaluationCase] = Field(min_length=1, max_length=50)
    top_k: int = Field(default=5, ge=1, le=12)
    workspace_id: int | None = Field(default=None, ge=1)
    knowledge_base_id: int | None = Field(default=None, ge=1)
    dataset_label: str = Field(default="用户标注的小样本评测", max_length=200)
    retrieval_mode: Literal["lexical", "semantic", "hybrid"] | None = None


def _scope(db, workspace_id, knowledge_base_id):
    context = workspace_context(db, workspace_id)
    kb = get_knowledge_base_or_default(db, context, knowledge_base_id)
    return context, kb


def _documents_query(db, context, kb):
    return db.query(KnowledgeDocument).filter(KnowledgeDocument.workspace_id == context.workspace_id,
                                            KnowledgeDocument.knowledge_base_id == kb.id)


def _document(db, document_id, context, kb):
    document = _documents_query(db, context, kb).filter(KnowledgeDocument.id == document_id).first()
    if document is None:
        raise HTTPException(404, "文档不存在或不属于当前知识库")
    return document


def _out(document, db, *, details=False):
    chunks = db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document.id).order_by(KnowledgeChunk.chunk_index).all()
    result = {"id": document.id, "document_id": document.id, "workspace_id": document.workspace_id,
              "knowledge_base_id": document.knowledge_base_id, "title": document.title,
              "source_uri": document.source_uri or "", "source_id": document.source_id,
              "content_hash": document.content_hash, "status": document.status, "chunk_count": len(chunks),
              "created_at": document.created_at.isoformat() if document.created_at else None,
              "updated_at": document.updated_at.isoformat() if document.updated_at else None,
              "metadata": document.metadata_json or {}}
    state = document_evidence_states(db, [document])[document.id]
    result["evidence_eligible"] = state["eligible"]
    result["metadata"] = {**result["metadata"], "evidence": state["evidence"], "freshness": state["freshness"]}
    if details:
        source = db.query(Source).filter(Source.id == document.source_id).first() if document.source_id else None
        result["content"] = (source.raw_content or "") if source else ""
        result["chunks"] = [{"id": chunk.id, "chunk_index": chunk.chunk_index, "content": chunk.content,
                             "start_index": chunk.start_index, "token_count": chunk.token_count,
                             "embedding_provider": chunk.embedding_provider,
                             "embedding_model": chunk.embedding_model} for chunk in chunks]
    return result


def _failure(exc):
    return HTTPException(503 if isinstance(exc, RetrievalError) else 422, str(exc))


@router.post("/import-preview")
async def preview_document_import(request: Request, db: Session = Depends(get_db)):
    """Extract only; confirmation uses DocumentUpload and its normal quota gate."""
    # Also bound local-mode bodies before JSON/base64 allocation. SaaS has an
    # earlier authentication/CSRF/role and body gate in its middleware.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(413, "请求内容过大，请选择 1 MiB 以内的文件。")
    try:
        body = DocumentImportPreview.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "导入参数无效，请重新选择 1 MiB 以内的文件及当前知识库。")
    try:
        from app.saas.context import is_saas_mode
        if is_saas_mode():
            from app.saas.connections import require_connection
            require_connection("markdown")
        context, kb = _scope(db, body.workspace_id, body.knowledge_base_id)
        preview = await run_in_threadpool(parse_document, body.filename, body.content_base64)
        return {**preview, "workspace_id": context.workspace_id, "knowledge_base_id": kb.id,
                "indexed": False, "next_action": "检查提取正文后确认入库，才会建立检索索引。"}
    except (ValueError, DocumentParseError) as exc:
        raise _failure(exc)


@router.get("/status")
def knowledge_status(workspace_id: int | None = None, knowledge_base_id: int | None = None, db: Session = Depends(get_db)):
    try:
        context, kb = _scope(db, workspace_id, knowledge_base_id)
        docs = _documents_query(db, context, kb).all()
        status = retrieval_status()
        return {**status, "workspace_id": context.workspace_id, "knowledge_base_id": kb.id,
                "evidence_health": evidence_health(document_evidence_states(db, docs)),
                "document_count": len(docs),
                "chunk_count": db.query(KnowledgeChunk).filter(KnowledgeChunk.workspace_id == context.workspace_id,
                    KnowledgeChunk.knowledge_base_id == kb.id).count(),
                "needs_reindex_count": sum((doc.metadata_json or {}).get("index_fingerprint") != status.get("index_fingerprint") for doc in docs)}
    except ValueError as exc:
        raise _failure(exc)


@router.get("/documents")
def list_documents(workspace_id: int | None = None, knowledge_base_id: int | None = None,
                   limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    try:
        context, kb = _scope(db, workspace_id, knowledge_base_id)
        query = _documents_query(db, context, kb)
        return {"items": [_out(doc, db) for doc in query.order_by(KnowledgeDocument.updated_at.desc()).offset(offset).limit(limit)],
                "total": query.count()}
    except ValueError as exc:
        raise _failure(exc)


@router.post("/documents", status_code=201)
def upload_document(body: DocumentUpload, db: Session = Depends(get_db)):
    from app.saas.context import is_saas_mode
    if is_saas_mode():
        from app.saas.connections import require_connection
        require_connection("obsidian" if body.source_uri.startswith("obsidian:") else "markdown")
    try:
        context, kb = _scope(db, body.workspace_id, body.knowledge_base_id)
        with INDEX_LOCK:
            source = None
            document = _document(db, body.document_id, context, kb) if body.document_id else None
            digest = hashlib.sha256(body.content.encode()).hexdigest()
            if not document:
                duplicate = _documents_query(db, context, kb).filter(KnowledgeDocument.content_hash == digest).first()
                if duplicate:
                    # Also upgrades a duplicate whose retrieval configuration has changed.
                    result = index_source(duplicate.source_id, db, context, kb.id)
                    return {"document": _out(duplicate, db), "deduplicated": True, "index": result}
            if document:
                source = db.query(Source).filter(Source.id == document.source_id).first()
                if source is None:
                    raise HTTPException(409, "文档原始素材缺失，请重新上传新文档")
                if source.source_type == "evidence_note" or (document.metadata_json or {}).get("evidence"):
                    raise HTTPException(409, "核验笔记不可在资料页改写；请新建修订笔记并重新核验")
                # Legacy sources may be referenced by another workspace/KB. Use copy-on-write.
                shared = db.query(KnowledgeDocument).filter(KnowledgeDocument.source_id == source.id,
                    KnowledgeDocument.id != document.id).count()
                if shared:
                    source = None
            if source is None:
                source = Source(source_type=body.format, title=body.title, raw_content=body.content,
                                summary=body.content[:220], url=body.source_uri)
                db.add(source)
                db.flush()
                if document:
                    document.source_id = source.id
                    db.flush()
            else:
                source.title, source.raw_content, source.summary = body.title, body.content, body.content[:220]
                source.url, source.source_type = body.source_uri, body.format
            result = index_source(source.id, db, context, kb.id)
            document = _document(db, result["document_id"], context, kb)
            return {"document": _out(document, db), "deduplicated": result["deduplicated"], "index": result}
    except ValueError as exc:
        db.rollback()
        raise _failure(exc)
    except Exception:
        db.rollback()
        raise


@router.get("/documents/{document_id}")
def get_document(document_id: int, workspace_id: int | None = None, knowledge_base_id: int | None = None, db: Session = Depends(get_db)):
    try:
        context, kb = _scope(db, workspace_id, knowledge_base_id)
        return _out(_document(db, document_id, context, kb), db, details=True)
    except ValueError as exc:
        raise _failure(exc)


@router.post("/documents/{document_id}/reindex")
def reindex_document(document_id: int, workspace_id: int | None = None, knowledge_base_id: int | None = None, db: Session = Depends(get_db)):
    try:
        context, kb = _scope(db, workspace_id, knowledge_base_id)
        document = _document(db, document_id, context, kb)
        return index_source(document.source_id, db, context, kb.id, ingestion_profile="force")
    except ValueError as exc:
        db.rollback()
        raise _failure(exc)


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, workspace_id: int | None = None, knowledge_base_id: int | None = None, db: Session = Depends(get_db)):
    try:
        context, kb = _scope(db, workspace_id, knowledge_base_id)
        with INDEX_LOCK:
            document = _document(db, document_id, context, kb)
            metadata = document.metadata_json or {}
            chunks = db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == document.id).all()
            ids = [chunk.id for chunk in chunks]
            for note in db.query(EvidenceNote).filter(EvidenceNote.document_id == document.id):
                note.document_id, note.index_state, note.indexed_at = None, "not_indexed", None
            for chunk in chunks:
                db.delete(chunk)
            db.delete(document)
            db.commit()
            cleanup_pending = False
            if metadata.get("vector_collection"):
                try:
                    vector_store.delete_points(retrieval_config(), metadata["vector_collection"], ids)
                except RetrievalError:
                    cleanup_pending = True
            # Source is deliberately retained: legacy content drafts may refer to it.
            return {"deleted": True, "id": document_id, "source_retained": True,
                    "vector_cleanup_pending": cleanup_pending}
    except ValueError as exc:
        db.rollback()
        raise _failure(exc)


@router.get("/demo-dataset")
def demo_dataset():
    path = Path(__file__).resolve().parents[4] / "scripts" / "fixtures" / "rag_demo.json"
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/evaluate")
def evaluate_knowledge(body: EvaluationRequest, db: Session = Depends(get_db)):
    try:
        context, kb = _scope(db, body.workspace_id, body.knowledge_base_id)
        docs = {doc.id for doc in _documents_query(db, context, kb).all()}
        chunks = {chunk.id: chunk.document_id for chunk in db.query(KnowledgeChunk).filter(
            KnowledgeChunk.workspace_id == context.workspace_id, KnowledgeChunk.knowledge_base_id == kb.id).all()}
        results, recalls, precisions, chunk_recalls, refusal_results, answer_results = [], [], [], [], [], []
        checked_citations = []
        top1_recalls, reciprocal_ranks, adopted_recalls = [], [], []
        adopted_relevant_count = 0
        adopted_evidence_count = 0
        for case in body.cases:
            expected_docs, expected_chunks = set(case.expected_document_ids), set(case.expected_chunk_ids)
            if not expected_docs.issubset(docs) or not expected_chunks.issubset(chunks):
                raise ValueError("评测标注文档或知识块不存在于当前知识库")
            started = perf_counter()
            answer = answer_question(case.question, db, context, kb.id, provider="local", top_k=body.top_k,
                                     retrieval_mode=body.retrieval_mode)
            citations = answer["citations"]
            candidates = answer["retrieval_candidates"]
            retrieved_docs = {item["document_id"] for item in candidates}
            retrieved_chunks = {item["chunk_id"] for item in candidates}
            adopted_docs = {item["document_id"] for item in citations}
            recall = len(expected_docs & retrieved_docs) / len(expected_docs) if expected_docs else None
            chunk_recall = len(expected_chunks & retrieved_chunks) / len(expected_chunks) if expected_chunks else None
            relevant = sum(item["document_id"] in expected_docs or item["chunk_id"] in expected_chunks for item in candidates)
            precision = relevant / len(candidates) if candidates else 0.0
            adopted_relevant = sum(item["document_id"] in expected_docs or item["chunk_id"] in expected_chunks for item in citations)
            adopted_relevant_count += adopted_relevant
            adopted_evidence_count += len(citations)
            adopted_precision = adopted_relevant / len(citations) if citations else None
            adopted_recall = len(expected_docs & adopted_docs) / len(expected_docs) if expected_docs else None
            top1_recall = len(expected_docs & {item["document_id"] for item in candidates[:1]}) / len(expected_docs) if expected_docs else None
            reciprocal_rank = next((1 / rank for rank, item in enumerate(candidates, 1)
                if item["document_id"] in expected_docs or item["chunk_id"] in expected_chunks), 0.0)
            citation_checks = [item["chunk_id"] in chunks and chunks[item["chunk_id"]] == item["document_id"] for item in citations]
            checked_citations.extend(citation_checks)
            if case.should_refuse:
                refusal_results.append(answer["refused"])
            else:
                if recall is not None:
                    recalls.append(recall)
                if chunk_recall is not None:
                    chunk_recalls.append(chunk_recall)
                precisions.append(precision)
                answer_results.append(not answer["refused"])
                reciprocal_ranks.append(reciprocal_rank)
                if top1_recall is not None:
                    top1_recalls.append(top1_recall)
                    adopted_recalls.append(adopted_recall)
            results.append({"question": case.question, "should_refuse": case.should_refuse,
                "refused": answer["refused"], "refusal_reason": answer["refusal_reason"],
                "recall_at_k": recall, "chunk_recall_at_k": chunk_recall,
                "evidence_precision_at_k": precision if not case.should_refuse else None,
                "candidate_precision_at_k": precision if not case.should_refuse else None,
                "candidate_recall_at_1": top1_recall, "reciprocal_rank": reciprocal_rank if not case.should_refuse else None,
                "answer_evidence_precision": adopted_precision, "answer_evidence_recall": adopted_recall,
                "candidate_count": len(candidates), "adopted_evidence_count": len(citations),
                "false_refusal": not case.should_refuse and answer["refused"],
                "citation_ids_valid": all(citation_checks) if citation_checks else None,
                "retrieved_document_ids": sorted(retrieved_docs), "retrieved_chunk_ids": sorted(retrieved_chunks),
                "latency_ms": round((perf_counter() - started) * 1000, 1),
                "retrieval_candidates": candidates, "citations": citations})
        mean = lambda values: round(sum(values) / len(values), 4) if values else None
        metrics = {"recall_at_k": mean(recalls), "chunk_recall_at_k": mean(chunk_recalls),
                   "evidence_precision_at_k": mean(precisions), "refusal_accuracy": mean(refusal_results),
                   "candidate_precision_at_k": mean(precisions), "candidate_recall_at_1": mean(top1_recalls),
                   "mean_reciprocal_rank": mean(reciprocal_ranks),
                   "answer_evidence_precision": round(adopted_relevant_count / adopted_evidence_count, 4) if adopted_evidence_count else None,
                   "answer_evidence_recall": mean(adopted_recalls),
                   "answerable_acceptance_rate": mean(answer_results),
                   "citation_validity_rate": mean(checked_citations),
                   "mean_latency_ms": mean([item["latency_ms"] for item in results])}
        return {"dataset_label": body.dataset_label, "sample_count": len(results), "top_k": body.top_k,
                "answerable_count": len(answer_results), "refusal_case_count": len(refusal_results),
                "checked_citation_count": len(checked_citations),
                "adopted_evidence_count": adopted_evidence_count,
                "retrieval": retrieval_status(body.retrieval_mode), "metrics": metrics, "cases": results,
                "metric_definitions": {
                    "recall_at_k": "可回答样本中，top-k候选包含的标注文档比例，按样本取均值。未先做证据阈值筛选。",
                    "candidate_recall_at_1": "可回答样本中，排名第一候选包含的标注文档比例，按样本取均值。",
                    "evidence_precision_at_k": "兼容旧字段，等于candidate_precision_at_k；是候选相关比例，不是答案采用证据精度。",
                    "answer_evidence_precision": "实际被回答引用且与标注相关的证据数 / 所有实际引用证据数；无引用返回null。",
                    "answer_evidence_recall": "可回答样本中，实际引用覆盖的标注文档比例，误拒按0计，按样本取均值。",
                },
                "limitations": ["小样本检索评测；不代表真实用户或生产效果。",
                    "候选召回与答案采用证据分别统计，阈值未为演示集调整，误拒样本保留。",
                    "使用原文摘录回答，未测试生成模型质量。", "引用有效率仅检查ID存在，证据相关性按人工标注计算，未自动验证蕴含关系。"]}
    except ValueError as exc:
        raise _failure(exc)

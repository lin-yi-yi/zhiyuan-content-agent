"""Scoped reviewed parameter directory, independent of queries and embeddings."""
import hashlib

from sqlalchemy.orm import Session

from app.agent_core.boundaries import WorkspaceContext, get_knowledge_base_or_default, require_capability
from app.agent_core.evidence_policy import document_evidence_states
from app.agent_core.rag_service import SearchHit, available_reviewed_facts
from app.models.evidence_note import fact_text
from app.models.knowledge_base import KnowledgeChunk, KnowledgeDocument


def fact_catalog(db: Session, context: WorkspaceContext, knowledge_base_id: int | None = None,
                 offset: int = 0, limit: int = 30) -> dict:
    """List available labels, never infer what a question requires or return values."""
    require_capability("rag_retrieve", "search")
    if offset < 0 or not 1 <= limit <= 50:
        raise ValueError("目录分页参数无效")
    kb = get_knowledge_base_or_default(db, context, knowledge_base_id)
    documents = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.workspace_id == context.workspace_id,
        KnowledgeDocument.knowledge_base_id == kb.id,
        KnowledgeDocument.status == "indexed",
    ).order_by(KnowledgeDocument.id).all()
    # Only governed, structurally valid metadata can participate in this directory.
    # Ordinary uploads remain usable for general retrieval, not parameter suggestions.
    documents = [doc for doc in documents if isinstance(doc.metadata_json, dict)
                 and isinstance(doc.metadata_json.get("evidence"), dict)]
    try:
        states = document_evidence_states(db, documents)
    except (TypeError, AttributeError):
        # Malformed historical JSON must not bypass live conflict/review checks.
        # Fail the directory explicitly instead of presenting a partial safe list.
        raise ValueError("资料结构无效，暂时无法提供参数目录，请检查核验笔记。") from None
    documents = {doc.id: doc for doc in documents if states[doc.id]["eligible"]}
    grouped = {}
    if documents:
        chunks = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.workspace_id == context.workspace_id,
            KnowledgeChunk.knowledge_base_id == kb.id,
            KnowledgeChunk.document_id.in_(documents),
        ).order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index, KnowledgeChunk.id)
        for chunk in chunks.yield_per(200):
            if hashlib.sha256(chunk.content.encode()).hexdigest() != chunk.embedding_hash:
                continue
            doc = documents[chunk.document_id]
            hit = SearchHit(chunk_id=chunk.id, document_id=doc.id, knowledge_base_id=kb.id,
                            workspace_id=context.workspace_id, source_id=doc.source_id,
                            title=doc.title, source_uri=doc.source_uri or "", content=chunk.content,
                            score=0, chunk_index=chunk.chunk_index,
                            metadata={"evidence": states[doc.id]["evidence"]})
            for fact in available_reviewed_facts([hit]):
                if any(len(fact[field].strip()) > 200 for field in ("product_model", "parameter")):
                    continue  # Options must remain valid inputs to RequiredFact.
                key = (fact_text(fact["product_model"]), fact_text(fact["parameter"]))
                item = grouped.setdefault(key, {"product_model": fact["product_model"],
                                                "parameter": fact["parameter"], "evidence": []})
                source = {field: fact[field] for field in
                          ("document_id", "chunk_id", "source_url", "locator", "version_label")}
                if source not in item["evidence"]:
                    item["evidence"].append(source)
    ordered = [grouped[key] for key in sorted(grouped)]
    return {"workspace_id": context.workspace_id, "knowledge_base_id": kb.id,
            "items": ordered[offset:offset + limit], "total": len(ordered),
            "offset": offset, "limit": limit, "has_more": offset + limit < len(ordered),
            "requirements_complete": False, "suggestion_method": "reviewed_evidence_metadata"}

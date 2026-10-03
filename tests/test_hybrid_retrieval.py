"""Behavior contracts with real local Qdrant and synthetic vectors (not quality proof)."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agent_core import embeddings, rag_service, vector_store
from app.agent_core.boundaries import workspace_context, get_knowledge_base_or_default
from app.agent_core.hybrid_retrieval import bm25_rank, reciprocal_rank_fusion, tokenize
from app.agent_core.tools import execute_tool
from app.api.routes.knowledge import DocumentUpload, upload_document, router as knowledge_router
from app.api.routes.v04 import router as v04_router
from app.db.session import Base, get_db
from app.models.evidence_note import EvidenceNote
from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.models.workspace import Workspace
from app.services.evidence_notes import NoteCreate, NoteReview, NoteScope, create_note, review_note, index_note


CONTENT = "编号 E-401 对应虚构星桥设备的证书失效告警。请先校准设备时间，再重新签发证书，不能通过删除审计日志绕过校验。"


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "semantic")
    monkeypatch.setenv("RAG_VECTOR_PATH", str(tmp_path / "vectors"))
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    monkeypatch.setattr(rag_service, "embed_texts", lambda texts, config, **kwargs: [[1.0, 0.0] for _ in texts])
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    vector_store.close_vector_stores()
    engine.dispose()


def upload(db, content=CONTENT, **scope):
    return upload_document(DocumentUpload(title="合成混合检索测试", content=content, **scope), db)["document"]


def search(db, query="E-401", **kwargs):
    return rag_service.search_knowledge(query, db, workspace_context(db), retrieval_mode="hybrid", **kwargs)


def test_bm25_keeps_exact_identifiers_and_chinese_without_cross_punctuation_terms():
    tokens = tokenize("E-401 API_KEY v1.5 深圳，开发")
    assert {"e-401", "api_key", "v1.5", "深圳", "开发"}.issubset(tokens)
    assert "圳开" not in tokens
    items = bm25_rank("E-401", [(1, "E-400 其他告警"), (2, CONTENT), (3, "E-4010 相似编号")], 5)
    assert [item["id"] for item in items] == [2]
    assert items[0]["term_overlap"] == 1.0


def test_bm25_uses_length_normalization_and_diminishing_term_frequency():
    items = bm25_rank("证书", [(1, "证书"), (2, "证书" + "无关背景" * 100), (3, "证书 证书")], 5)
    scores = {item["id"]: item["score"] for item in items}
    assert scores[1] > scores[2]
    assert scores[3] < 2 * scores[1]


def test_rrf_deduplicates_and_uses_rank_not_raw_score():
    dense = [{"id": 1, "score": 0.6}, {"id": 2, "score": 0.5}, {"id": 2, "score": 0.5}]
    lexical = [{"id": 2, "score": 9000.0, "term_overlap": 1.0, "matched_term_count": 1, "query_term_count": 1}]
    result = reciprocal_rank_fusion(dense, lexical, 5)
    assert [item["id"] for item in result] == [2, 1]
    assert result[0]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)
    lexical[0]["score"] = 0.0001
    assert reciprocal_rank_fusion(dense, lexical, 5)[0]["rrf_score"] == result[0]["rrf_score"]


def test_hybrid_recalls_exact_identifier_missed_by_dense_and_uses_independent_gate(db, monkeypatch):
    monkeypatch.setattr(rag_service, "embed_texts", lambda texts, config, **kwargs:
        [[1.0, 0.0] if kwargs.get("query") else [0.0, 1.0] for _ in texts])
    document = upload(db)
    assert rag_service.search_knowledge("E-401", db, workspace_context(db), retrieval_mode="semantic") == []
    hits = search(db)
    assert hits[0].document_id == document["id"]
    assert hits[0].score < 0.08  # An RRF score must not be tested against a cosine cutoff.
    assert hits[0].metadata["scores"]["semantic_rank"] is None
    assert hits[0].metadata["scores"]["bm25_rank"] == 1
    assert hits[0].metadata["evidence_gate"]["lexical_pass"]
    assert rag_service.select_evidence(hits) == hits
    answer = rag_service.answer_question("E-401", db, workspace_context(db), retrieval_mode="hybrid")
    assert not answer["refused"] and answer["retrieval"]["strategy"] == "hybrid_bm25_rrf"


def test_rrf_top_rank_cannot_turn_weak_unrelated_candidate_into_evidence(db, monkeypatch):
    monkeypatch.setattr(rag_service, "embed_texts", lambda texts, config, **kwargs:
        [[1.0, 0.0] if kwargs.get("query") else [0.3, (1 - 0.3 ** 2) ** 0.5] for _ in texts])
    upload(db)
    hits = search(db, "远古火星海洋")
    assert len(hits) == 1 and hits[0].metadata["scores"]["semantic_rank"] == 1
    assert not hits[0].metadata["evidence_gate"]["eligible"]
    assert rag_service.select_evidence(hits) == []
    assert rag_service.answer_question("远古火星海洋", db, workspace_context(db), retrieval_mode="hybrid")["refused"]


def test_both_routes_and_bm25_corpus_statistics_are_scope_filtered(db):
    first = upload(db)
    before = search(db)[0].metadata["scores"]
    context = workspace_context(db)
    second_kb = KnowledgeBase(workspace_id=context.workspace_id, name="其他知识库")
    other_workspace = Workspace(name="其他空间", slug="hybrid-other", is_default=False)
    db.add_all([second_kb, other_workspace])
    db.commit()
    upload(db, CONTENT + "其他知识库的合成信息。", knowledge_base_id=second_kb.id)
    upload(db, CONTENT + "其他工作空间的合成信息。", workspace_id=other_workspace.id)
    after = search(db)
    assert [hit.document_id for hit in after] == [first["id"]]
    assert after[0].metadata["scores"] == before


@pytest.mark.parametrize("state", ["revoked", "expired", "changed"])
def test_ineligible_evidence_is_removed_before_both_routes_and_idf(db, state):
    good = upload(db)
    before = search(db)[0].metadata["scores"]["bm25_score"]
    created = create_note(NoteCreate(title="核验合成编号", content=CONTENT + "额外的已核验说明。",
        source_url="https://example.com/hybrid", version_label="synthetic-v1", rights_basis="own",
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
        citations=[{"claim": "证书失效告警", "excerpt": "编号 E-401 对应虚构星桥设备的证书失效告警。",
                    "source_url": "https://example.com/hybrid"}]), db)
    review_note(created["id"], NoteReview(decision="verify", confirmed_sources=True), db)
    indexed = index_note(created["id"], NoteScope(), db)
    note = db.get(EvidenceNote, created["id"])
    doc = db.get(KnowledgeDocument, indexed["document_id"])
    if state == "revoked":
        review_note(note.id, NoteReview(decision="revoke"), db)
        doc.status = "indexed"  # Simulate a stale restored snapshot.
    elif state == "expired":
        note.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    else:
        doc.content_hash = "0" * 64
    db.commit()
    hits = search(db)
    assert [hit.document_id for hit in hits] == [good["id"]]
    assert hits[0].metadata["scores"]["bm25_score"] == before


def test_semantic_index_is_reused_without_global_mode_mutation(db):
    document = upload(db)
    chunk_id = db.query(KnowledgeChunk).one().id
    assert search(db)[0].chunk_id == chunk_id
    assert embeddings.retrieval_status()["mode"] == "semantic"
    assert db.get(KnowledgeDocument, document["id"]).metadata_json["retrieval_mode"] == "semantic"
    tool = execute_tool("rag.search", {"query": "E-401"}, db, workspace_context(db))
    assert tool["output"]["strategy"] == "semantic_cosine"
    hybrid = execute_tool("rag.search", {"query": "E-401", "retrieval_mode": "hybrid"}, db, workspace_context(db))
    assert hybrid["output"]["strategy"] == "hybrid_bm25_rrf"


def test_hybrid_does_not_silently_fall_back_when_embedding_fails(db, monkeypatch):
    upload(db)
    def fail(*args, **kwargs):
        raise embeddings.RetrievalError("合成语义故障")
    monkeypatch.setattr(rag_service, "embed_texts", fail)
    with pytest.raises(embeddings.RetrievalError, match="语义故障"):
        search(db)


def test_explicit_hybrid_environment_indexes_vectors_and_legacy_lexical_still_works(db, monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "hybrid")
    upload(db)
    assert db.query(KnowledgeChunk).one().embedding_dim == 2
    assert embeddings.retrieval_status()["semantic_enabled"]
    assert search(db)
    legacy = rag_service.search_knowledge("证书", db, workspace_context(db), retrieval_mode="lexical")
    assert legacy[0].metadata["scores"]["strategy"] == "lexical_overlap"


def test_api_search_answer_evaluation_mode_contract_and_validation(db):
    document = upload(db)
    application = FastAPI()
    application.include_router(knowledge_router, prefix="/api")
    application.include_router(v04_router, prefix="/api")
    application.dependency_overrides[get_db] = lambda: db
    with TestClient(application) as client:
        for endpoint in ("search", "answer"):
            response = client.post(f"/api/v04/rag/{endpoint}", json={"query": "E-401", "retrieval_mode": "hybrid"})
            assert response.status_code == 200, response.text
            assert response.json()["retrieval"]["mode"] == "hybrid"
            assert client.post(f"/api/v04/rag/{endpoint}", json={"query": "E-401", "retrieval_mode": "invented"}).status_code == 422
        evaluated = client.post("/api/knowledge/evaluate", json={"retrieval_mode": "hybrid", "cases": [
            {"question": "E-401", "expected_document_ids": [document["id"]]}]})
        assert evaluated.status_code == 200, evaluated.text
        assert evaluated.json()["retrieval"]["strategy"] == "hybrid_bm25_rrf"
        assert evaluated.json()["metrics"]["recall_at_k"] == 1.0

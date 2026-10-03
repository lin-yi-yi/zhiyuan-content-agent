"""Contract tests: synthetic embedding vectors test plumbing, not semantic quality.

Qdrant runs for real in a temporary folder. All SQL records are temporary SQLite.
Real model retrieval is measured separately with scripts/evaluate_rag.py.
"""
import os
from pathlib import Path
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.agent_core import embeddings, rag_service, vector_store
from app.agent_core.boundaries import workspace_context, get_knowledge_base_or_default
from app.api.routes.knowledge import router, DocumentUpload, upload_document
from app.db.session import Base, get_db
from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.models.source import Source
from app.models.workspace import Workspace


CONTENT = "技术内容需要使用官方文档作为证据。每个结论应标注原始来源。证据不足时必须提示缺少资料，不允许编造出处。"


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    monkeypatch.setenv("RAG_VECTOR_PATH", str(tmp_path / "qdrant"))
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    vector_store.close_vector_stores()
    engine.dispose()


@pytest.fixture
def client(db):
    application = FastAPI()
    application.include_router(router, prefix="/api")
    application.dependency_overrides[get_db] = lambda: db
    with TestClient(application) as result:
        yield result


def upload(db, content=CONTENT, **kwargs):
    return upload_document(DocumentUpload(title="测试资料", content=content, **kwargs), db)["document"]


def scope(db):
    context = workspace_context(db)
    return context, get_knowledge_base_or_default(db, context)


def test_lexical_mode_never_creates_fake_vectors_and_refuses_unrelated(db, monkeypatch):
    monkeypatch.setattr(rag_service, "embed_texts", lambda *a, **kw: pytest.fail("lexical must not embed"))
    document = upload(db)
    context, kb = scope(db)
    chunk = db.query(KnowledgeChunk).one()
    assert chunk.embedding_json is None and chunk.embedding_dim == 0
    assert chunk.embedding_provider == "none"
    answer = rag_service.answer_question("技术内容证据来源", db, context, kb.id)
    assert not answer["refused"]
    assert answer["retrieval"]["mode"] == "lexical"
    assert answer["citations"][0]["document_id"] == document["id"]
    assert answer["citation_check"]["valid"]
    assert rag_service.answer_question("月球地质成分", db, context, kb.id)["refused"]


def test_upload_dedup_and_replacement_remove_old_chunks(db):
    document = upload(db)
    before_ids = {item.id for item in db.query(KnowledgeChunk)}
    duplicate = upload_document(DocumentUpload(title="另一个标题", content=CONTENT), db)
    assert duplicate["deduplicated"]
    assert duplicate["document"]["id"] == document["id"]
    assert db.query(KnowledgeDocument).count() == 1
    updated = upload(db, "收藏率的计算方式是收藏数量除以浏览量。浏览量为零时显示缺失值。周报应使用相同统计周期，不得混用不同平台数据。", document_id=document["id"])
    assert updated["id"] == document["id"]
    assert not before_ids.intersection(item.id for item in db.query(KnowledgeChunk))
    context, kb = scope(db)
    assert rag_service.search_knowledge("收藏率", db, context, kb.id)[0].document_id == document["id"]
    assert rag_service.search_knowledge("官方文档", db, context, kb.id) == []


def test_scope_filters_search_detail_delete_and_copy_on_write(db, client):
    document = upload(db)
    workspace = Workspace(name="隔离区", slug="isolated", is_default=False)
    db.add(workspace)
    db.commit()
    context = workspace_context(db, workspace.id)
    kb = get_knowledge_base_or_default(db, context)
    assert rag_service.search_knowledge("证据来源", db, context, kb.id) == []
    assert client.get(f"/api/knowledge/documents/{document['id']}", params={"workspace_id": workspace.id}).status_code == 404
    assert client.delete(f"/api/knowledge/documents/{document['id']}", params={"workspace_id": workspace.id}).status_code == 404
    # Shared legacy source gets a separate source when one KB uploads an edit.
    shared = rag_service.index_source(document["source_id"], db, context, kb.id)
    upload(db, CONTENT + "这是当前知识库独有的新增内容。", document_id=document["id"])
    remote_doc = db.get(KnowledgeDocument, shared["document_id"])
    assert db.get(Source, remote_doc.source_id).raw_content == CONTENT


def test_semantic_contract_real_qdrant_and_model_mismatch(db, monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "semantic")
    monkeypatch.setattr(rag_service, "embed_texts", lambda texts, config, **kwargs: [[1.0, 0.0, 0.0] for _ in texts])
    document = upload(db)
    context, kb = scope(db)
    hits = rag_service.search_knowledge("问题", db, context, kb.id)
    assert hits[0].document_id == document["id"]
    assert hits[0].metadata["retrieval_mode"] == "semantic"
    assert hits[0].metadata["evidence_threshold"] == 0.55
    assert hits[0].score == pytest.approx(1.0)
    assert db.query(KnowledgeChunk).one().embedding_json is None
    vector_store.close_vector_stores()
    assert rag_service.search_knowledge("持久化后仍可检索", db, context, kb.id)
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "changed-model")
    with pytest.raises(embeddings.RetrievalError, match="重新索引"):
        rag_service.search_knowledge("问题", db, context, kb.id)


def test_semantic_failure_preserves_previous_source_and_index(db, monkeypatch):
    document = upload(db)
    original_hash = document["content_hash"]
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "semantic")
    def fail(*args, **kwargs):
        raise embeddings.RetrievalError("模拟模型故障")
    monkeypatch.setattr(rag_service, "embed_texts", fail)
    with pytest.raises(Exception):
        upload(db, CONTENT + "这是失败更新不应保存的额外内容。", document_id=document["id"])
    db.expire_all()
    assert db.get(KnowledgeDocument, document["id"]).content_hash == original_hash
    assert db.get(Source, document["source_id"]).raw_content == CONTENT


def test_semantic_rejects_old_hash_and_lexical_index(db, monkeypatch):
    upload(db)
    context, kb = scope(db)
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "semantic")
    monkeypatch.setattr(rag_service, "embed_texts", lambda *a, **kw: pytest.fail("must detect index mismatch first"))
    with pytest.raises(embeddings.RetrievalError, match="不会混用"):
        rag_service.search_knowledge("证据", db, context, kb.id)


def test_llm_answer_rejects_invented_citation(db, monkeypatch):
    upload(db)
    context, kb = scope(db)
    class FakeLLM:
        def chat(self, *args, **kwargs):
            return "未经支持的结论 [chunk:999999]"
    monkeypatch.setattr(rag_service.llm_router, "get_task_client", lambda *a, **kw: FakeLLM())
    result = rag_service.answer_question("证据来源", db, context, kb.id, provider="test")
    assert result["refused"] and not result["citation_check"]["valid"]


def test_upload_limits_and_delete_lifecycle(client, db):
    assert client.post("/api/knowledge/documents", json={"title": "   ", "content": CONTENT}).status_code == 422
    assert client.post("/api/knowledge/documents", json={"title": "标题", "content": CONTENT, "source_uri": "file:///etc/passwd"}).status_code == 422
    assert client.post("/api/knowledge/documents", json={"title": "标题", "content": "x" * 120001}).status_code == 422
    document = upload(db)
    detail = client.get(f"/api/knowledge/documents/{document['id']}").json()
    assert detail["content"] == CONTENT and detail["chunks"]
    response = client.delete(f"/api/knowledge/documents/{document['id']}")
    assert response.status_code == 200
    assert response.json()["source_retained"]
    assert db.query(KnowledgeChunk).count() == 0
    assert client.get(f"/api/knowledge/documents/{document['id']}").status_code == 404


def test_evaluation_recall_denominator_and_unknown_labels(client, db):
    first = upload(db)
    second = upload(db, "收藏率的计算方式是收藏数量除以浏览量。浏览量为零时显示缺失值。周报应使用相同统计周期，不得混用不同平台数据。")
    response = client.post("/api/knowledge/evaluate", json={"top_k": 1, "cases": [
        {"question": "官方文档证据", "expected_document_ids": [first["id"], second["id"]]},
        {"question": "月球地质成分", "should_refuse": True},
    ]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["metrics"]["recall_at_k"] == 0.5  # Recall is not merely any-hit rate.
    assert result["metrics"]["refusal_accuracy"] == 1.0
    assert result["metrics"]["citation_validity_rate"] == 1.0
    assert client.post("/api/knowledge/evaluate", json={"cases": [{"question": "问", "expected_document_ids": [99999]}]}).status_code == 422


def test_invalid_semantic_config_and_bad_vectors(monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "semantic")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "test")
    monkeypatch.delenv("RAG_EMBEDDING_BASE_URL", raising=False)
    with pytest.raises(embeddings.RetrievalError):
        embeddings.retrieval_config()
    assert not embeddings.retrieval_status()["configured"]
    for values in ([[0.0, 0.0]], [[float("nan"), 1]], [[1], [1, 2]], []):
        with pytest.raises(embeddings.RetrievalError):
            embeddings._validate_vectors(values, 1)


def test_demo_dataset_is_fixed_synthetic_resource(client):
    result = client.get("/api/knowledge/demo-dataset")
    assert result.status_code == 200
    assert len(result.json()["documents"]) == 4
    assert "非生产效果" in result.json()["label"]


def test_semantic_reindex_and_delete_clear_live_vectors(db, client, monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "semantic")
    monkeypatch.setattr(rag_service, "embed_texts", lambda texts, config, **kwargs: [[1.0, 0.0] for _ in texts])
    document = upload(db)
    context, kb = scope(db)
    old_id = db.query(KnowledgeChunk).one().id
    response = client.post(f"/api/knowledge/documents/{document['id']}/reindex")
    assert response.status_code == 200, response.text
    hits = rag_service.search_knowledge("问题", db, context, kb.id)
    assert hits and all(hit.chunk_id != old_id for hit in hits)
    assert client.delete(f"/api/knowledge/documents/{document['id']}").status_code == 200
    assert rag_service.search_knowledge("问题", db, context, kb.id) == []


def test_openai_embedding_contract_orders_indexes_and_redacts_errors(monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "semantic")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "example-embedding")
    monkeypatch.setenv("RAG_EMBEDDING_BASE_URL", "https://embeddings.invalid/v1")
    monkeypatch.setenv("RAG_EMBEDDING_API_KEY", "test-secret-never-display")
    config = embeddings.retrieval_config()
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            assert url == "https://embeddings.invalid/v1/embeddings"
            assert kwargs["json"]["input"] == ["first", "second"]
            return Response()
    monkeypatch.setattr(embeddings.httpx, "Client", FakeClient)
    assert embeddings.embed_texts(["first", "second"], config) == [[1.0, 0.0], [0.0, 1.0]]
    def fail(self, *args, **kwargs):
        raise RuntimeError("test-secret-never-display and private document")
    monkeypatch.setattr(FakeClient, "post", fail)
    with pytest.raises(embeddings.RetrievalError) as caught:
        embeddings.embed_texts(["first", "second"], config)
    assert "test-secret" not in str(caught.value) and "private document" not in str(caught.value)


def test_candidate_recall_is_separate_from_answer_evidence_and_preserves_badcase(db, client, monkeypatch):
    first = upload(db)
    second = upload(db, "收藏率的计算方式是收藏数量除以浏览量。浏览量为零时显示缺失值。周报应使用相同统计周期，不得混用不同平台数据。")
    context, kb = scope(db)
    docs = {doc.id: doc for doc in db.query(KnowledgeDocument)}
    candidates = [rag_service.SearchHit(
        chunk_id=chunk.id, document_id=chunk.document_id, knowledge_base_id=kb.id,
        workspace_id=context.workspace_id, source_id=docs[chunk.document_id].source_id,
        title=docs[chunk.document_id].title, source_uri="", content=chunk.content,
        score=0.8 if chunk.document_id == first["id"] else 0.4, chunk_index=0,
        metadata={"retrieval_mode": "semantic", "evidence_threshold": 0.55},
    ) for chunk in db.query(KnowledgeChunk).order_by(KnowledgeChunk.id)]
    monkeypatch.setattr(rag_service, "search_knowledge", lambda *a, **kw: candidates)
    answer = rag_service.answer_question("问", db, context, kb.id)
    assert len(answer["retrieval_candidates"]) == 2
    assert [item["document_id"] for item in answer["citations"]] == [first["id"]]
    assert str(second["id"]) not in [str(item["document_id"]) for item in answer["citations"]]
    request = {"top_k": 2, "cases": [{"question": "问", "expected_document_ids": [first["id"]]}]}
    evaluated = client.post("/api/knowledge/evaluate", json=request).json()
    assert evaluated["metrics"]["candidate_precision_at_k"] == 0.5
    assert evaluated["metrics"]["answer_evidence_precision"] == 1.0
    candidates[0].score = 0.54
    refused = client.post("/api/knowledge/evaluate", json=request).json()
    assert refused["metrics"]["recall_at_k"] == 1.0
    assert refused["metrics"]["answer_evidence_recall"] == 0.0
    assert refused["metrics"]["answer_evidence_precision"] is None
    assert refused["cases"][0]["false_refusal"] is True
    assert refused["cases"][0]["citations"] == []

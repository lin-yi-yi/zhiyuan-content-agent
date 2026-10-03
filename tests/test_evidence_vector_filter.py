"""Real local Qdrant filtering with synthetic vectors, never model/news calls."""
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.agent_core import rag_service, vector_store
from app.agent_core.boundaries import get_knowledge_base_or_default, workspace_context
from app.agent_core.embeddings import RetrievalConfig
from app.db.session import Base
from app.models.evidence_note import EvidenceNote
from app.models.knowledge_base import KnowledgeChunk, KnowledgeDocument
from app.models.source import Source


WORKSPACE = 7
KNOWLEDGE_BASE = 9
VALID_DOCUMENT = 56
CONTENT = "合成星桥证据规范：到期或撤销的笔记必须在向量排序前排除，仍然有效的证据应当能够被检索到。所有向量仅用于验证过滤边界。"
CONTENT_HASH = hashlib.sha256(CONTENT.encode()).hexdigest()


@pytest.fixture
def config(tmp_path):
    result = RetrievalConfig(
        mode="semantic", provider="fastembed", model="synthetic-vector-filter-test",
        base_url="", api_key="", cache_dir=str(tmp_path / "unused-model-cache"),
        vector_path=str(tmp_path / "qdrant"), semantic_threshold=0.55,
    )
    yield result
    vector_store.close_vector_stores()


def chunk(identifier, document_id, *, workspace_id=WORKSPACE, knowledge_base_id=KNOWLEDGE_BASE):
    return SimpleNamespace(
        id=identifier, document_id=document_id, workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id, embedding_hash=CONTENT_HASH,
    )


def populate_vectors(config):
    chunks = [chunk(index, index) for index in range(1, 56)]
    chunks.append(chunk(56, VALID_DOCUMENT))
    # These deliberately reuse the requested document ID in foreign scopes:
    # document eligibility must be ANDed with both existing scope filters.
    chunks.extend([
        chunk(57, VALID_DOCUMENT, workspace_id=WORKSPACE + 1),
        chunk(58, VALID_DOCUMENT, knowledge_base_id=KNOWLEDGE_BASE + 1),
    ])
    vectors = [[1.0, 0.0]] * 55 + [[0.8, 0.6], [1.0, 0.0], [1.0, 0.0]]
    vector_store.upsert_chunks(config, chunks, vectors, CONTENT_HASH)


def test_document_filter_recalls_valid_evidence_before_candidate_limit(config):
    populate_vectors(config)
    unfiltered = vector_store.search_vectors(config, [1.0, 0.0], WORKSPACE, KNOWLEDGE_BASE, 40)
    assert len(unfiltered) == 40
    assert VALID_DOCUMENT not in {point["payload"]["document_id"] for point in unfiltered}
    assert all(point["score"] == pytest.approx(1.0) for point in unfiltered)

    filtered = vector_store.search_vectors(
        config, [1.0, 0.0], WORKSPACE, KNOWLEDGE_BASE, 40, document_ids=[VALID_DOCUMENT],
    )
    assert [point["id"] for point in filtered] == [56]
    assert filtered[0]["score"] == pytest.approx(0.8)
    assert filtered[0]["payload"]["workspace_id"] == WORKSPACE
    assert filtered[0]["payload"]["knowledge_base_id"] == KNOWLEDGE_BASE


def test_none_preserves_unrestricted_document_selection_with_scope_isolation(config):
    populate_vectors(config)
    implicit = vector_store.search_vectors(config, [1.0, 0.0], WORKSPACE, KNOWLEDGE_BASE, 100)
    explicit = vector_store.search_vectors(
        config, [1.0, 0.0], WORKSPACE, KNOWLEDGE_BASE, 100, document_ids=None,
    )
    assert {point["id"] for point in implicit} == set(range(1, 57))
    assert explicit == implicit
    assert all(point["payload"]["workspace_id"] == WORKSPACE for point in explicit)
    assert all(point["payload"]["knowledge_base_id"] == KNOWLEDGE_BASE for point in explicit)


def test_empty_document_filter_never_opens_qdrant(config, monkeypatch):
    def forbidden_client(*args, **kwargs):
        pytest.fail("an empty eligibility set must not open or create Qdrant")

    monkeypatch.setattr(vector_store, "_client", forbidden_client)
    assert vector_store.search_vectors(
        config, [1.0, 0.0], WORKSPACE, KNOWLEDGE_BASE, 40, document_ids=[],
    ) == []
    assert not Path(config.vector_path).exists()


def test_rag_excludes_expired_notes_before_qdrant_candidate_ranking(config, monkeypatch):
    monkeypatch.setattr(rag_service, "retrieval_config", lambda: config)
    monkeypatch.setattr(rag_service, "embed_texts", lambda texts, *args, **kwargs: [[1.0, 0.0] for _ in texts])
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as db:
            context = workspace_context(db)
            kb = get_knowledge_base_or_default(db, context)
            now = datetime.now(timezone.utc)
            chunks, vectors = [], []
            valid_document_id = None
            for index in range(56):
                source = Source(
                    source_type="evidence_note", title=f"合成星桥笔记 {index}",
                    raw_content=CONTENT, summary=CONTENT,
                    url=f"https://example.com/synthetic-evidence/{index}",
                )
                db.add(source)
                db.flush()
                document = KnowledgeDocument(
                    workspace_id=context.workspace_id, knowledge_base_id=kb.id,
                    source_id=source.id, title=source.title, source_uri=source.url,
                    content_hash=CONTENT_HASH, status="indexed", ingestion_profile="semantic-v2",
                )
                db.add(document)
                db.flush()
                note = EvidenceNote(
                    workspace_id=context.workspace_id, knowledge_base_id=kb.id,
                    title=source.title, content=CONTENT, content_hash=CONTENT_HASH,
                    source_url=source.url, provider="manual", version_label="synthetic-v1",
                    citations=[{"claim": "有效证据应被检索", "excerpt": CONTENT, "source_url": source.url}],
                    rights_basis="own", rights_note="", review_status="verified", verified_at=now,
                    source_id=source.id, document_id=document.id, index_state="indexed",
                    expires_at=now + timedelta(days=1) if index == 55 else now - timedelta(days=1),
                )
                db.add(note)
                db.flush()
                document.metadata_json = {
                    "index_fingerprint": config.fingerprint, "source_type": "evidence_note",
                    "evidence": {"note_id": note.id},
                }
                knowledge_chunk = KnowledgeChunk(
                    workspace_id=context.workspace_id, knowledge_base_id=kb.id,
                    document_id=document.id, chunk_index=0, content=CONTENT,
                    embedding_hash=CONTENT_HASH, embedding_dim=2,
                    embedding_provider=config.provider, embedding_model=config.model,
                    metadata_json={"index_fingerprint": config.fingerprint},
                )
                db.add(knowledge_chunk)
                db.flush()
                chunks.append(knowledge_chunk)
                vectors.append([0.8, 0.6] if index == 55 else [1.0, 0.0])
                if index == 55:
                    valid_document_id = document.id
            db.commit()
            vector_store.upsert_chunks(config, chunks, vectors, CONTENT_HASH)

            before_filter = vector_store.search_vectors(config, [1.0, 0.0], context.workspace_id, kb.id, 40)
            assert len(before_filter) == 40
            assert valid_document_id not in {point["payload"]["document_id"] for point in before_filter}
            hits = rag_service.search_knowledge("合成星桥有效证据", db, context, kb.id, top_k=1)
            assert [hit.document_id for hit in hits] == [valid_document_id]
            assert hits[0].score == pytest.approx(0.8)
            assert hits[0].metadata["evidence"]["status"] == "verified"
            assert hits[0].metadata["freshness"]["status"] != "expired"
    finally:
        engine.dispose()

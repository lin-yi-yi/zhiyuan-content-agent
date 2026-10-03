"""Review, provenance and expiry invariants across note and RAG services."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import app.models  # noqa: F401
from app.db.session import Base
from app.models.evidence_note import EvidenceNote
from app.models.knowledge_base import KnowledgeDocument
from app.models.source import Source
from app.agent_core.boundaries import workspace_context
from app.agent_core import rag_service
from app.api.routes.knowledge import DocumentUpload, upload_document, get_document, delete_document
from app.services.evidence_notes import NoteCreate, NoteReview, NoteScope, create_note, review_note, index_note


CONTENT = "合成星桥系统规范：星桥审阅资料保留来源链接、版本和结论摘录。星桥撤销核验后，相关资料立即停止参与检索回答。星桥规范只描述本测试的虚构系统。"


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


def indexed_note(db):
    note = create_note(NoteCreate(title="合成星桥规范", content=CONTENT,
        source_url="https://example.com/xingqiao", version_label="synthetic-v1", rights_basis="own",
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
        citations=[{"claim": "撤销后停止检索", "excerpt": "星桥撤销核验后，相关资料立即停止参与检索回答。",
                    "source_url": "https://example.com/xingqiao#review"}]), db)
    review_note(note["id"], NoteReview(decision="verify", confirmed_sources=True), db)
    result = index_note(note["id"], NoteScope(), db)
    return db.get(EvidenceNote, note["id"]), db.get(KnowledgeDocument, result["document_id"])


def answer(db, **kwargs):
    return rag_service.answer_question("星桥", db, workspace_context(db), **kwargs)


def test_answer_exposes_claim_citations_versions_and_expiry(db):
    note, document = indexed_note(db)
    result = answer(db)
    assert not result["refused"]
    citation = result["citations"][0]
    assert citation["metadata"]["evidence"]["note_id"] == note.id
    assert citation["metadata"]["evidence"]["version_label"] == "synthetic-v1"
    assert citation["metadata"]["evidence"]["citations"][0]["claim"] == "撤销后停止检索"
    assert citation["metadata"]["freshness"]["status"] == "expires_soon"
    assert result["evidence_health"]["expires_soon_count"] == 1


def test_expiration_excludes_evidence_and_explains_refusal(db):
    note, document = indexed_note(db)
    note.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()  # Simulate passage of time without editing the immutable content.
    result = answer(db)
    assert result["refused"] and result["citations"] == []
    assert result["evidence_health"]["expired_count"] == 1
    detail = get_document(document.id, db=db)
    assert not detail["evidence_eligible"]
    assert detail["metadata"]["freshness"]["status"] == "expired"


def test_revoked_sql_state_overrides_an_indexed_metadata_snapshot(db):
    note, document = indexed_note(db)
    review_note(note.id, NoteReview(decision="revoke", note="合成撤销"), db)
    document.status = "indexed"
    document.metadata_json = {**document.metadata_json, "evidence": {"note_id": note.id, "status": "verified"}}
    db.commit()  # Even a stale/restored metadata snapshot cannot grant approval.
    result = answer(db)
    assert result["refused"]
    assert result["evidence_health"]["unverified_count"] == 1


@pytest.mark.parametrize("mutation", ["missing_metadata", "hash_mismatch", "indexing"])
def test_incomplete_or_changed_evidence_fails_closed(db, mutation):
    note, document = indexed_note(db)
    if mutation == "missing_metadata":
        document.metadata_json = {}
    elif mutation == "hash_mismatch":
        document.content_hash = "0" * 64
    else:
        note.index_state = "indexing"
    db.commit()
    assert answer(db)["refused"]


def test_reindex_preserves_governance_and_generic_edit_cannot_rewrite_review(db):
    note, document = indexed_note(db)
    rag_service.index_source(note.source_id, db, workspace_context(db), ingestion_profile="force")
    assert db.get(KnowledgeDocument, document.id).metadata_json["evidence"]["note_id"] == note.id
    assert not answer(db)["refused"]
    with pytest.raises(HTTPException) as error:
        upload_document(DocumentUpload(title="修改", content=CONTENT + "未经核验变更。", document_id=document.id), db)
    assert error.value.status_code == 409
    review_note(note.id, NoteReview(decision="revoke"), db)
    with pytest.raises(ValueError, match="核验"):
        rag_service.index_source(note.source_id, db, workspace_context(db), ingestion_profile="force")


def test_legacy_upload_is_labelled_untracked_instead_of_claiming_verified(db):
    upload_document(DocumentUpload(title="普通资料", content=CONTENT), db)
    result = answer(db)
    assert not result["refused"]
    assert result["evidence_health"]["untracked_count"] == 1
    assert result["citations"][0]["metadata"]["evidence"] is None


def test_revocation_during_generation_discards_answer(db, monkeypatch):
    note, _ = indexed_note(db)
    class DelayedModel:
        def chat(self, *args, **kwargs):
            hits = rag_service.search_knowledge("星桥", db, workspace_context(db))
            review_note(note.id, NoteReview(decision="revoke"), db)
            return f"合成结论 [chunk:{hits[0].chunk_id}]"
    monkeypatch.setattr(rag_service.llm_router, "get_task_client", lambda *args, **kwargs: DelayedModel())
    result = answer(db, provider="test")
    assert result["refused"] and result["refusal_reason"] == "evidence_changed"
    assert result["citations"] == []


def test_deleting_index_updates_note_and_explicit_reimport_restores_it(db):
    note, document = indexed_note(db)
    delete_document(document.id, db=db)
    db.refresh(note)
    assert note.document_id is None and note.index_state == "not_indexed"
    with pytest.raises(ValueError, match="核验"):
        rag_service.index_source(note.source_id, db, workspace_context(db))
    rebuilt = index_note(note.id, NoteScope(), db)
    assert rebuilt["note"]["index_status"] == "indexed"
    assert not answer(db)["refused"]

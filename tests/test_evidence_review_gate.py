"""Human review requires current document eligibility, not only retained chunks."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.routes.knowledge import DocumentUpload, upload_document
from app.db.session import Base
from app.models.agent_run import AgentRun
from app.models.card import Card
from app.models.draft import Draft
from app.models.evidence_note import EvidenceNote
from app.models.knowledge_base import KnowledgeChunk, KnowledgeDocument
from app.models.topic import Topic
from app.services.evidence_notes import (
    NoteCreate, NoteReview, NoteScope, create_note, index_note, review_note,
)
from app.services.review_lifecycle import validate_review_evidence
from app.services.workflow_support import WorkflowConflict


CONTENT = (
    "合成星桥审核规范：所有结论均须保留资料引用和人工核验记录。"
    "星桥资料被撤销、拒绝或者到期后，既有引用不得继续用于审核。"
    "本资料仅描述测试中的虚构系统，不包含真实新闻。"
)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


def indexed_note(db, suffix="primary"):
    created = create_note(NoteCreate(
        title=f"合成审核资料 {suffix}",
        content=CONTENT,
        source_url=f"https://example.com/review/{suffix}",
        rights_basis="own",
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
        citations=[{
            "claim": "资料失效后不得继续审核",
            "excerpt": "星桥资料被撤销、拒绝或者到期后，既有引用不得继续用于审核。",
            "source_url": f"https://example.com/review/{suffix}#policy",
        }],
    ), db)
    review_note(created["id"], NoteReview(decision="verify", confirmed_sources=True), db)
    result = index_note(created["id"], NoteScope(), db)
    document = db.get(KnowledgeDocument, result["document_id"])
    chunk = db.query(KnowledgeChunk).filter_by(document_id=document.id).first()
    assert chunk is not None
    return db.get(EvidenceNote, created["id"]), document, chunk


def review_snapshot(db, chunk, *extra_chunks):
    topic = Topic(title="合成引用审核")
    db.add(topic)
    db.flush()
    draft = Draft(topic_id=topic.id, body_text=f"合成结论 [chunk:{chunk.id}]")
    db.add(draft)
    db.flush()
    run = AgentRun(goal="审核合成引用", draft_id=draft.id, result_json={
        "_request": {"use_rag": True},
        "rag_context": {
            "workspace_id": chunk.workspace_id,
            "knowledge_base_id": chunk.knowledge_base_id,
        },
        "citations": [{
            "chunk_id": item.id,
            "document_id": item.document_id,
            "excerpt": item.content[:1200],
        } for item in (chunk, *extra_chunks)],
    })
    db.add(run)
    db.commit()
    return run, draft


def assert_retained_chunk(db, chunk, run):
    current = db.get(KnowledgeChunk, chunk.id)
    assert current is not None
    citation = next(item for item in run.result_json["citations"] if item["chunk_id"] == chunk.id)
    assert current.content[:1200] == citation["excerpt"]


def test_verified_indexed_note_can_pass_human_review(db):
    note, document, chunk = indexed_note(db)
    run, draft = review_snapshot(db, chunk)
    assert note.review_status == "verified" and document.status == "indexed"
    assert validate_review_evidence(run, draft, db) is None


@pytest.mark.parametrize("decision", ["revoke", "reject"])
def test_withdrawn_note_blocks_review_despite_retained_unchanged_chunk(db, decision):
    note, _, chunk = indexed_note(db)
    run, draft = review_snapshot(db, chunk)
    validate_review_evidence(run, draft, db)
    review_note(note.id, NoteReview(decision=decision), db)
    assert_retained_chunk(db, chunk, run)
    with pytest.raises(WorkflowConflict, match="重新生成"):
        validate_review_evidence(run, draft, db)


def test_note_expiring_after_generation_blocks_review(db):
    note, document, chunk = indexed_note(db)
    run, draft = review_snapshot(db, chunk)
    validate_review_evidence(run, draft, db)
    note.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert document.status == "indexed"
    assert_retained_chunk(db, chunk, run)
    with pytest.raises(WorkflowConflict, match="重新生成"):
        validate_review_evidence(run, draft, db)


def test_ordinary_uploaded_document_remains_reviewable(db):
    uploaded = upload_document(DocumentUpload(title="普通合成资料", content=CONTENT), db)
    document = db.get(KnowledgeDocument, uploaded["document"]["id"])
    assert not (document.metadata_json or {}).get("evidence")
    chunk = db.query(KnowledgeChunk).filter_by(document_id=document.id).first()
    run, draft = review_snapshot(db, chunk)
    assert validate_review_evidence(run, draft, db) is None


def test_missing_document_blocks_review_even_when_chunk_survives(db):
    _, document, chunk = indexed_note(db)
    run, draft = review_snapshot(db, chunk)
    # This isolated SQLite fixture permits a dangling chunk, as after an incomplete restore.
    db.delete(document)
    db.commit()
    assert db.get(KnowledgeDocument, chunk.document_id) is None
    assert_retained_chunk(db, chunk, run)
    with pytest.raises(WorkflowConflict, match="重新生成"):
        validate_review_evidence(run, draft, db)


def test_card_only_reference_to_disabled_document_blocks_review(db):
    _, _, primary_chunk = indexed_note(db)
    _, card_document, card_chunk = indexed_note(db, "card")
    run, draft = review_snapshot(db, primary_chunk, card_chunk)
    card = Card(draft_id=draft.id, page_index=1, card_type="content", title="合成卡片",
                body=f"卡片独立引用 [chunk:{card_chunk.id}]")
    db.add(card)
    db.commit()
    validate_review_evidence(run, draft, db)
    card_document.status = "disabled"
    db.commit()
    assert_retained_chunk(db, card_chunk, run)
    with pytest.raises(WorkflowConflict, match="重新生成"):
        validate_review_evidence(run, draft, db)
    db.delete(card)
    db.commit()
    # The draft itself cites only the still-eligible document.
    assert validate_review_evidence(run, draft, db) is None


@pytest.mark.parametrize("field", ["selected_title", "selected_cover_text", "comment_guide", "hashtags"])
def test_reference_outside_body_must_belong_to_retrieved_evidence(db, field):
    _, _, chunk = indexed_note(db)
    run, draft = review_snapshot(db, chunk)
    text = "伪造片段 [chunk:999999]"
    setattr(draft, field, [text] if field == "hashtags" else text)
    db.commit()
    with pytest.raises(WorkflowConflict, match="引用"):
        validate_review_evidence(run, draft, db)


def test_title_only_reference_to_expired_evidence_blocks_review(db):
    _, _, primary = indexed_note(db)
    title_note, _, title_chunk = indexed_note(db, "title")
    run, draft = review_snapshot(db, primary, title_chunk)
    draft.selected_title = f"标题独立引用 [chunk:{title_chunk.id}]"
    db.commit()
    assert validate_review_evidence(run, draft, db) is None
    title_note.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(WorkflowConflict, match="重新生成"):
        validate_review_evidence(run, draft, db)


@pytest.mark.parametrize("field,value", [
    ("title_options", {"primary": "旧版字典标题 [chunk:999999]"}),
    ("title_options", [{"title": "旧版对象标题 [chunk:999999]"}]),
    ("hashtags", {"items": ["旧版话题 [chunk:999999]"]}),
    ("hashtags", {"hashtags": ["旧版话题 [chunk:999999]"]}),
])
def test_legacy_dictionary_copy_cannot_hide_unretrieved_references(db, field, value):
    _, _, chunk = indexed_note(db)
    run, draft = review_snapshot(db, chunk)
    setattr(draft, field, value)
    db.commit()
    with pytest.raises(WorkflowConflict, match="引用"):
        validate_review_evidence(run, draft, db)

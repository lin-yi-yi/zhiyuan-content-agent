"""Synthetic evidence-note review/index contracts using isolated SQLite only."""
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.agent_core import rag_service, vector_store
from app.agent_core.boundaries import get_knowledge_base_or_default, workspace_context
from app.db.session import Base, get_db
from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.models.source import Source
from app.models.workspace import Workspace
from app.services import evidence_notes as evidence
from app.api.routes import evidence as evidence_routes


CONTENT = (
    "合成测试规范：证据笔记必须由用户核对原始引用后才能进入知识库。"
    "只有本人原创或已获许可的材料可以索引。被拒绝、撤销或过期的笔记不能继续作为回答证据。"
)
BASE = "/api/evidence/notes"


@pytest.fixture
def database(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    monkeypatch.setenv("RAG_VECTOR_PATH", str(tmp_path / "qdrant"))
    monkeypatch.setattr(rag_service, "embed_texts", lambda *a, **kw: pytest.fail("lexical tests must not call embeddings"))
    engine = create_engine(
        f"sqlite:///{tmp_path / 'evidence-notes.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    vector_store.close_vector_stores()
    engine.dispose()


@pytest.fixture
def client(database):
    application = FastAPI()
    application.include_router(evidence_routes.router, prefix="/api")

    def sessions():
        with database() as db:
            yield db

    application.dependency_overrides[get_db] = sessions
    with TestClient(application, raise_server_exceptions=False) as result:
        yield result


def note_payload(**changes):
    now = datetime.now(timezone.utc)
    body = {
        "title": "合成证据笔记",
        "content": CONTENT,
        "source_url": "https://example.com/synthetic-evidence",
        "provider": "manual",
        "version_label": "synthetic-v1",
        "published_at": (now - timedelta(days=1)).isoformat(),
        "expires_at": (now + timedelta(days=7)).isoformat(),
        "citations": [{
            "claim": "只有本人原创或已获许可的材料可以索引",
            "excerpt": "只有本人原创或已获许可的材料可以索引。",
            "source_url": "https://example.com/synthetic-evidence",
            "locator": "合成规范第二句",
        }],
        "rights_basis": "own",
    }
    body.update(changes)
    return body


def create(client, **changes):
    result = client.post(BASE, json=note_payload(**changes))
    assert result.status_code == 201, result.text
    return result.json()


def verify(client, note, **changes):
    body = {"decision": "verify", "confirmed_sources": True, "note": "已核对合成引用"}
    body.update(changes)
    result = client.post(f"{BASE}/{note['id']}/review", json=body)
    assert result.status_code == 200, result.text
    return result.json()


def current_note(client, note_id, **scope):
    result = client.get(BASE, params=scope)
    assert result.status_code == 200, result.text
    return next(item for item in result.json()["items"] if item["id"] == note_id)


def indexed_counts(factory):
    with factory() as db:
        return (
            db.query(Source).count(),
            db.query(KnowledgeDocument).count(),
            db.query(KnowledgeChunk).count(),
        )


def search(factory, *, workspace_id=None, knowledge_base_id=None):
    with factory() as db:
        context = workspace_context(db, workspace_id)
        kb = get_knowledge_base_or_default(db, context, knowledge_base_id)
        return rag_service.search_knowledge("证据笔记 原始引用 知识库", db, context, kb.id)


def test_create_never_verifies_or_indexes_and_pending_index_is_blocked(client, database):
    note = create(client)
    assert note["review_status"] == "pending"
    assert note["index_status"] == "not_indexed"
    assert note["document_id"] is None
    assert indexed_counts(database) == (0, 0, 0)
    blocked = client.post(f"{BASE}/{note['id']}/index", json={})
    assert blocked.status_code == 409, blocked.text
    assert indexed_counts(database) == (0, 0, 0)


@pytest.mark.parametrize("confirmed", [None, False])
def test_verification_requires_explicit_source_confirmation(client, database, confirmed):
    note = create(client)
    body = {"decision": "verify", "note": "不能代替明确确认"}
    if confirmed is not None:
        body["confirmed_sources"] = confirmed
    blocked = client.post(f"{BASE}/{note['id']}/review", json=body)
    assert blocked.status_code == 409, blocked.text
    assert current_note(client, note["id"])["review_status"] == "pending"
    assert indexed_counts(database) == (0, 0, 0)


@pytest.mark.parametrize("changes", [
    {"citations": []},
    {"rights_basis": "reference_only"},
    {"rights_basis": "permission", "rights_note": ""},
])
def test_verification_requires_citation_and_sufficient_rights(client, database, changes):
    note = create(client, **changes)
    blocked = client.post(f"{BASE}/{note['id']}/review", json={
        "decision": "verify", "confirmed_sources": True,
    })
    assert blocked.status_code == 409, blocked.text
    assert current_note(client, note["id"])["review_status"] == "pending"
    assert indexed_counts(database) == (0, 0, 0)


@pytest.mark.parametrize("rights_basis,rights_note", [
    ("own", ""), ("permission", "合成授权：允许本项目将该测试笔记用于检索"),
])
def test_valid_manual_review_is_separate_from_indexing(client, database, rights_basis, rights_note):
    note = create(client, rights_basis=rights_basis, rights_note=rights_note)
    reviewed = verify(client, note)
    assert reviewed["review_status"] == "verified"
    assert reviewed["index_status"] == "not_indexed"
    assert reviewed["review_note"] == "已核对合成引用"
    assert indexed_counts(database) == (0, 0, 0)


def test_aihot_reference_requires_own_note_confirmation_and_stays_pending(client, database):
    blocked = client.post(BASE, json=note_payload(provider="aihot", own_note_confirmed=False))
    assert blocked.status_code == 422, blocked.text
    note = create(client, provider="aihot", own_note_confirmed=True)
    assert note["review_status"] == "pending"
    assert note["index_status"] == "not_indexed"
    assert indexed_counts(database) == (0, 0, 0)


@pytest.mark.parametrize("field", ["source_url", "citation_url"])
@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "file:///etc/passwd", "http://127.0.0.1/private",
    "https://user:secret@example.com/private",
])
def test_unsafe_source_and_citation_urls_are_rejected(client, database, field, url):
    body = note_payload()
    if field == "citation_url":
        body["citations"][0]["source_url"] = url
    else:
        body["source_url"] = url
    result = client.post(BASE, json=body)
    assert result.status_code == 422, result.text
    assert indexed_counts(database) == (0, 0, 0)


@pytest.mark.parametrize("field", ["claim", "excerpt", "source_url"])
def test_incomplete_citation_cannot_enter_verified_state(client, field):
    body = note_payload()
    body["citations"][0][field] = ""
    result = client.post(BASE, json=body)
    assert result.status_code == 422, result.text


def test_verified_index_is_idempotent_and_returns_real_scoped_evidence(client, database):
    note = verify(client, create(client))
    first = client.post(f"{BASE}/{note['id']}/index", json={})
    assert first.status_code == 200, first.text
    indexed = first.json()
    assert indexed["note"]["index_status"] == "indexed"
    assert indexed["document_id"] == indexed["note"]["document_id"]
    counts = indexed_counts(database)
    assert counts[0] == 1 and counts[1] == 1 and counts[2] > 0
    hits = search(database)
    assert hits and all(hit.document_id == indexed["document_id"] for hit in hits)
    assert all(hit.source_uri == note["source_url"] for hit in hits)
    second = client.post(f"{BASE}/{note['id']}/index", json={})
    assert second.status_code == 200, second.text
    assert second.json()["deduplicated"]
    assert second.json()["document_id"] == indexed["document_id"]
    assert indexed_counts(database) == counts


def test_source_and_citation_anchors_survive_review_and_index_metadata(client, database):
    source_url = "https://example.com/synthetic-evidence#section-2"
    citation_url = "https://example.com/synthetic-evidence#sentence-2"
    citations = note_payload()["citations"]
    citations[0]["source_url"] = citation_url
    note = create(client, source_url=source_url, citations=citations)
    for state in (note, verify(client, note)):
        assert state["source_url"] == source_url
        assert state["citations"][0]["source_url"] == citation_url
    indexed = client.post(f"{BASE}/{note['id']}/index", json={})
    assert indexed.status_code == 200, indexed.text
    output = indexed.json()
    assert output["note"]["source_url"] == source_url
    assert output["note"]["citations"][0]["source_url"] == citation_url
    with database() as db:
        document = db.get(KnowledgeDocument, output["document_id"])
        assert document.source_uri == source_url
        assert db.get(Source, document.source_id).url == source_url
        assert document.metadata_json["evidence"]["citations"][0]["source_url"] == citation_url
    hits = search(database)
    assert hits and all(hit.source_uri == source_url for hit in hits)


@pytest.mark.parametrize("decision,expected", [("reject", "rejected"), ("revoke", "revoked")])
def test_rejection_or_revocation_removes_previously_indexed_note_from_retrieval(client, database, decision, expected):
    note = verify(client, create(client))
    indexed = client.post(f"{BASE}/{note['id']}/index", json={})
    assert indexed.status_code == 200, indexed.text
    assert search(database)
    changed = client.post(f"{BASE}/{note['id']}/review", json={"decision": decision, "note": "合成撤销理由"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["review_status"] == expected
    assert changed.json()["index_status"] != "indexed"
    assert search(database) == []
    blocked = client.post(f"{BASE}/{note['id']}/index", json={})
    assert blocked.status_code == 409, blocked.text


def test_expired_note_cannot_be_indexed(client, database):
    note = create(client, expires_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    assert note["is_expired"]
    reviewed = client.post(f"{BASE}/{note['id']}/review", json={"decision": "verify", "confirmed_sources": True})
    assert reviewed.status_code == 409, reviewed.text
    blocked = client.post(f"{BASE}/{note['id']}/index", json={})
    assert blocked.status_code == 409, blocked.text
    assert indexed_counts(database) == (0, 0, 0)


@pytest.mark.parametrize("separate_workspace", [True, False], ids=["workspace", "knowledge-base"])
def test_scopes_isolate_note_listing_review_index_and_retrieval(client, database, separate_workspace):
    note = verify(client, create(client))
    assert client.post(f"{BASE}/{note['id']}/index", json={}).status_code == 200
    with database() as db:
        if separate_workspace:
            workspace = Workspace(name="合成隔离区", slug="evidence-isolated", is_default=False)
            db.add(workspace)
            db.commit()
            other_id = workspace.id
            context = workspace_context(db, other_id)
            other_kb = get_knowledge_base_or_default(db, context).id
        else:
            other_id = note["workspace_id"]
            kb = KnowledgeBase(workspace_id=other_id, name="同工作区合成隔离库", status="active")
            db.add(kb)
            db.commit()
            other_kb = kb.id
    scope = {"workspace_id": other_id, "knowledge_base_id": other_kb}
    listed = client.get(BASE, params=scope)
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []
    for action, extra in (("review", {"decision": "revoke"}), ("index", {})):
        denied = client.post(f"{BASE}/{note['id']}/{action}", json={**scope, **extra})
        assert denied.status_code == 404, denied.text
    assert search(database, **scope) == []
    assert search(database)
    assert current_note(client, note["id"])["review_status"] == "verified"


def test_index_failure_never_marks_indexed_and_can_be_retried(client, database, monkeypatch):
    note = verify(client, create(client))
    original = evidence.index_source

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-private-detail")

    monkeypatch.setattr(evidence, "index_source", fail)
    failed = client.post(f"{BASE}/{note['id']}/index", json={})
    assert failed.status_code == 503, failed.text
    assert "synthetic-private-detail" not in failed.text
    state = current_note(client, note["id"])
    assert state["index_status"] != "indexed"
    assert state["review_status"] == "verified"
    assert search(database) == []
    monkeypatch.setattr(evidence, "index_source", original)
    retried = client.post(f"{BASE}/{note['id']}/index", json={})
    assert retried.status_code == 200, retried.text
    assert retried.json()["note"]["index_status"] == "indexed"
    assert indexed_counts(database)[0:2] == (1, 1)
    assert search(database)


def test_index_and_revoke_serialize_under_local_index_lock(client, database, monkeypatch):
    note = verify(client, create(client))
    original = evidence.index_source
    index_entered, release_index = Event(), Event()
    revoke_started, revoke_finished = Event(), Event()

    def paused_index(*args, **kwargs):
        index_entered.set()
        assert release_index.wait(5), "index test was not released"
        return original(*args, **kwargs)

    def index():
        with database() as db:
            return evidence.index_note(note["id"], evidence.NoteScope(), db)

    def revoke():
        with database() as db:
            revoke_started.set()
            try:
                return evidence.review_note(note["id"], evidence.NoteReview(decision="revoke", note="并发撤销"), db)
            finally:
                revoke_finished.set()

    monkeypatch.setattr(evidence, "index_source", paused_index)
    with ThreadPoolExecutor(max_workers=2) as pool:
        index_future = pool.submit(index)
        try:
            assert index_entered.wait(3), "index did not reach the controlled boundary"
            revoke_future = pool.submit(revoke)
            assert revoke_started.wait(3)
            assert not revoke_finished.wait(0.15), "revoke crossed the index mutation lock"
        finally:
            release_index.set()
        index_future.result(timeout=5)
        revoke_future.result(timeout=5)
    final = current_note(client, note["id"])
    assert final["review_status"] == "revoked"
    assert final["index_status"] != "indexed"
    assert search(database) == []

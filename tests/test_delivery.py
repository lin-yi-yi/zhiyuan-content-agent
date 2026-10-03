"""Approved handoffs use real review/evidence gates and isolated SQLite data."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.routes.delivery import router
from app.db.session import Base, get_db
from app.models.agent_run import AgentRun, AgentStep
from app.models.card import Card
from app.models.draft import Draft
from app.models.evidence_note import EvidenceNote
from app.models.knowledge_base import KnowledgeChunk
from app.models.topic import Topic
from app.schemas.agent_run import AgentReviewCreate
from app.services.content_growth_agent import review_agent_run
from app.services.delivery import build_delivery
from app.services.evidence_notes import NoteCreate, NoteReview, NoteScope, create_note, index_note, review_note


CONTENT = "合成交付资料：本文描述虚构的星桥内容工作室。已审核内容必须保留引用；原资料过期或撤回后不能继续交付。INTERNAL_SOURCE_ONLY 仅留在内部核验摘录中。"


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    monkeypatch.setenv("SAAS_MODE", "false")
    engine = create_engine(f"sqlite:///{tmp_path / 'delivery.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def api(database):
    app = FastAPI()
    app.include_router(router, prefix="/api")

    def session():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        yield client


def seed(database, *, approve=True, use_rag=True):
    with database() as db:
        note = create_note(NoteCreate(
            title="星桥合成事实", content=CONTENT, source_url="https://example.com/guide?token=private-source-token",
            version_label="合成版本 2026-10",
            rights_basis="own", expires_at=datetime.now(timezone.utc) + timedelta(days=3),
            citations=[{"claim": "引用失效不能交付", "excerpt": "原资料过期或撤回后不能继续交付。",
                        "source_url": "https://example.com/guide", "locator": "合成手册第 2 节"}],
        ), db)
        review_note(note["id"], NoteReview(decision="verify", confirmed_sources=True), db)
        indexed = index_note(note["id"], NoteScope(), db)
        chunk = db.query(KnowledgeChunk).filter_by(document_id=indexed["document_id"]).first()
        topic = Topic(title="合成内容交付")
        db.add(topic)
        db.flush()
        draft = Draft(topic_id=topic.id, selected_title="已确认的合成标题", body_text=f"合成审核正文 [chunk:{chunk.id}]",
                      hashtags=["#合成测试"], aigc_notice="本文由 AI 辅助，已人工核对。", status="awaiting_review")
        db.add(draft)
        db.flush()
        card = Card(draft_id=draft.id, page_index=1, card_type="content", title="合成卡片",
                    body=f"资料整理 [chunk:{chunk.id}]")
        run = AgentRun(goal="合成交付", status="awaiting_review", current_step="human_review", draft_id=draft.id,
                       result_json={
                           "_request": {"use_rag": use_rag, "provider_api_key": "synthetic-private-key"},
                           "rag_context": {"workspace_id": chunk.workspace_id, "knowledge_base_id": chunk.knowledge_base_id},
                           "citations": [{"chunk_id": chunk.id, "document_id": chunk.document_id, "title": "合成事实资料",
                                          "source_uri": "https://example.com/guide?token=private-source-token",
                                          "excerpt": chunk.content[:1200], "raw_excerpt": "INTERNAL_SOURCE_ONLY"}],
                           "brief": {
                               "profile": {"id": 12, "name": "合成品牌", "version": 3, "audience": "测试读者", "tone": "克制",
                                           "prohibited_claims": "", "call_to_action": "阅读产品说明", "owner_email": "private@example.invalid",
                                           "secret": "profile-secret", "workspace_id": 1},
                               "workflow": {"key": "knowledge_post", "name": "知识解读", "description": "从授权资料整理",
                                            "required_materials": ["产品手册"], "instructions": "internal-prompt-not-for-export"},
                               "delivery": "图文交付包", "policy": {"facts_from_knowledge_only": True,
                               "human_review_required": True, "automatic_publish": False, "api_key": "private-policy-key"},
                               "internal_email": "private@example.invalid",
                           },
                       })
        db.add_all([card, run])
        db.flush()
        db.add(AgentStep(run_id=run.id, step_index=1, key="human_review", label="人工审核", status="awaiting_review"))
        db.commit()
        if approve:
            review_agent_run(run.id, AgentReviewCreate(decision="approve", note="private@example.invalid private-review-note"), db)
        return {"run": run.id, "draft": draft.id, "card": card.id, "note": note["id"], "chunk": chunk.id}


def test_approved_handoff_contains_snapshot_and_no_internal_metadata(database, api):
    ids = seed(database)
    response = api.get(f"/api/agent-runs/{ids['run']}/delivery")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert payload["delivery_mode"] == "manual"
    assert payload["title"] == "已确认的合成标题"
    assert payload["body_text"] == f"合成审核正文 [chunk:{ids['chunk']}]"
    assert payload["hashtags"] == ["#合成测试"]
    assert payload["brand_brief"]["profile"]["version"] == 3
    assert payload["brand_brief"]["workflow"]["required_materials"] == ["产品手册"]
    assert len(payload["citations"]) == 1
    citation = payload["citations"][0]
    assert citation["title"] == "合成事实资料" and citation["source_uri"] == "https://example.com/guide"
    assert citation["marker"] == f"[chunk:{ids['chunk']}]" and citation["chunk_id"] == ids["chunk"]
    assert citation["version_label"] == "合成版本 2026-10" and citation["verification_status"] == "verified"
    assert citation["document_id"] and citation["chunk_index"] == 0
    assert len(citation["document_content_hash"]) == 64 and citation["verified_at"] and citation["expires_at"]
    assert citation["locators"] == ["合成手册第 2 节"]
    assert set(payload["review"]) == {"at", "content_hash"}
    assert len(payload["review"]["content_hash"]) == 64
    assert "品牌快照版本：3" in payload["markdown"] and "工作流模板：知识解读" in payload["markdown"]
    assert "人工发布检查" in payload["markdown"] and "AI 辅助说明" in payload["markdown"]
    assert "资料版本：合成版本 2026-10" in payload["markdown"] and "合成手册第 2 节" in payload["markdown"]
    assert payload["evidence_metadata_basis"] == "current_at_delivery" and payload["evidence_checked_at"]
    assert "并非生成时元数据快照" in payload["markdown"]
    serialized = json.dumps(payload, ensure_ascii=False)
    for private in ("private@example.invalid", "private-source-token", "private-review-note", "synthetic-private-key",
                    "INTERNAL_SOURCE_ONLY", "profile-secret", "private-policy-key", "internal-prompt-not-for-export"):
        assert private not in serialized
    with database() as db:
        assert db.get(AgentRun, ids["run"]).status == "approved"


@pytest.mark.parametrize("status", ["pending", "running", "awaiting_review", "rejected", "failed", "cancelled", "completed"])
def test_only_approved_runs_can_be_handed_off(database, api, status):
    ids = seed(database, approve=False)
    with database() as db:
        db.get(AgentRun, ids["run"]).status = status
        db.commit()
    assert api.get(f"/api/agent-runs/{ids['run']}/delivery").status_code == 409


@pytest.mark.parametrize("entity", ["draft", "card"])
def test_editing_even_without_invalidation_blocks_handoff(database, api, entity):
    ids = seed(database)
    with database() as db:
        if entity == "draft":
            db.get(Draft, ids[entity]).body_text += " 未审核新增内容"
        else:
            db.get(Card, ids[entity]).title = "未审核新卡片标题"
        db.commit()
    response = api.get(f"/api/agent-runs/{ids['run']}/delivery")
    assert response.status_code == 409 and "不一致" in response.text


@pytest.mark.parametrize("change", ["expired", "revoked", "changed_chunk"])
def test_evidence_invalidated_after_approval_blocks_handoff(database, api, change):
    ids = seed(database)
    with database() as db:
        if change == "expired":
            db.get(EvidenceNote, ids["note"]).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif change == "revoked":
            review_note(ids["note"], NoteReview(decision="revoke"), db)
        else:
            db.get(KnowledgeChunk, ids["chunk"]).content = "原始资料已被替换"
        db.commit()
    response = api.get(f"/api/agent-runs/{ids['run']}/delivery")
    assert response.status_code == 409 and "引用" in response.text


def test_missing_run_is_404(database, api):
    assert api.get("/api/agent-runs/999999/delivery").status_code == 404
    with database() as db:
        assert build_delivery(999999, db) is None


def test_missing_draft_is_conflict(database, api):
    ids = seed(database)
    with database() as db:
        db.get(AgentRun, ids["run"]).draft_id = None
        db.commit()
    assert api.get(f"/api/agent-runs/{ids['run']}/delivery").status_code == 409


def test_missing_recorded_review_cannot_be_bypassed_by_status(database, api):
    ids = seed(database, approve=False)
    with database() as db:
        db.get(AgentRun, ids["run"]).status = "approved"
        db.commit()
    assert api.get(f"/api/agent-runs/{ids['run']}/delivery").status_code == 409


def test_old_run_without_brand_still_exports_and_unused_sources_do_not(database, api):
    ids = seed(database, use_rag=False)
    with database() as db:
        run = db.get(AgentRun, ids["run"])
        data = dict(run.result_json)
        data.pop("brief")
        data["citations"] = [*data["citations"], {"chunk_id": 9999, "title": "UNUSED_SOURCE", "source_uri": "file:///private/owner"}]
        run.result_json = data
        db.commit()
    response = api.get(f"/api/agent-runs/{ids['run']}/delivery")
    assert response.status_code == 200
    assert response.json()["brand_brief"] is None
    assert "未绑定品牌" in response.json()["markdown"]
    assert "UNUSED_SOURCE" not in response.text and "/private/owner" not in response.text


@pytest.mark.parametrize("source, expected", [
    ("https://mp.weixin.qq.com/s?__biz=synthetic&mid=123&idx=1&sn=public-checksum&token=secret",
     "https://mp.weixin.qq.com/s?__biz=synthetic&mid=123&idx=1&sn=public-checksum"),
    ("https://user:password@example.com/private", ""),
    ("file:///Users/private/client-notes.md", ""),
])
def test_source_attribution_keeps_public_identity_without_credentials(database, api, source, expected):
    ids = seed(database)
    with database() as db:
        run = db.get(AgentRun, ids["run"])
        data = dict(run.result_json)
        data["citations"] = [{**data["citations"][0], "source_uri": source}]
        run.result_json = data
        db.commit()
    response = api.get(f"/api/agent-runs/{ids['run']}/delivery")
    assert response.status_code == 200
    assert response.json()["citations"][0]["source_uri"] == expected


def test_internal_edit_snapshots_never_leave_in_delivery(database, api):
    from app.services.review_lifecycle import invalidate_review_for_edit

    ids = seed(database)
    with database() as db:
        draft = db.get(Draft, ids["draft"])
        draft.comment_guide = "INTERNAL_OLD_COPY"
        db.commit()
        invalidate_review_for_edit(draft, db, "合成修订")
        draft.comment_guide = "当前说明"
        db.commit()
        review_agent_run(ids["run"], AgentReviewCreate(decision="approve"), db)
        history = db.get(AgentRun, ids["run"]).result_json["content_history"]
        assert history[0]["snapshot"]["draft"]["comment_guide"] == "INTERNAL_OLD_COPY"
    response = api.get(f"/api/agent-runs/{ids['run']}/delivery")
    assert response.status_code == 200, response.text
    assert "content_history" not in response.text and "INTERNAL_OLD_COPY" not in response.text


@pytest.mark.parametrize("locator", [
    "file:///Users/alice/client-notes.txt", "/Users/alice/client-notes.txt", "位置：/private/client-notes.txt",
    "C:\\Users\\alice\\client-notes.txt", "\\\\internal-server\\client-files", "~/client-notes.txt",
    "../client-notes.txt", "位置：./private/client-notes.txt", "位于/Users/alice/client-notes.txt",
    "https://example.com/manual?token=private-locator-token", "https://alice:password@example.com/manual",
    "负责人 private-locator@example.invalid", "第2页 token=private-locator-token", "第2页\nprivate-locator-token",
])
def test_source_locator_does_not_export_internal_paths_or_credential_locations(database, api, locator):
    ids = seed(database)
    with database() as db:
        note = db.get(EvidenceNote, ids["note"])
        note.citations = [{**item, "locator": locator} for item in note.citations]
        db.commit()
    response = api.get(f"/api/agent-runs/{ids['run']}/delivery")
    assert response.status_code == 200, response.text
    assert response.json()["citations"][0]["locators"] == []
    assert "private-locator" not in response.text and "client-notes" not in response.text


@pytest.mark.parametrize("withdrawal", ["expiry", "revoke"])
def test_live_verified_conflict_blocks_export_even_without_an_indexed_peer(database, api, withdrawal):
    ids = seed(database)
    with database() as db:
        original = db.get(EvidenceNote, ids["note"])
        original.citations = [{**item, "product_model": "合成星桥", "parameter": "失效交付规则",
                               "value": "不能继续交付"} for item in original.citations]
        db.commit()
        proposed = create_note(NoteCreate(
            title="合成历史矛盾规则", version_label="synthetic-conflicting-v2", rights_basis="own",
            source_url="https://example.com/conflicting-rule",
            content="合成星桥测试材料：原资料过期或撤回后可以继续交付。这是一份明确错误的合成记录，用来模拟历史导入的脏数据，不是产品规则。",
            citations=[{"claim": "合成矛盾规则", "excerpt": "原资料过期或撤回后可以继续交付。",
                        "source_url": "https://example.com/conflicting-rule", "locator": "合成矛盾第1段",
                        "product_model": "合成星桥", "parameter": "失效交付规则", "value": "可以继续交付"}],
        ), db)
    # Pending claims do not supersede material already reviewed by a person.
    assert api.get(f"/api/agent-runs/{ids['run']}/delivery").status_code == 200
    with database() as db:
        peer = db.get(EvidenceNote, proposed["id"])
        # The public review API rejects this transition. Simulate an imported
        # historical conflict, including a verified peer that is not indexed.
        peer.review_status = "verified"
        peer.verified_at = datetime.now(timezone.utc)
        db.commit()
    response = api.get(f"/api/agent-runs/{ids['run']}/delivery")
    assert response.status_code == 409 and "冲突" in response.text
    with database() as db:
        if withdrawal == "expiry":
            db.get(EvidenceNote, proposed["id"]).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        else:
            review_note(proposed["id"], NoteReview(decision="revoke"), db)
    assert api.get(f"/api/agent-runs/{ids['run']}/delivery").status_code == 200

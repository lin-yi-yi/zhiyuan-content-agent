"""Variants cannot detach workflow content from its human-review lifecycle."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.api.routes.drafts import router
from app.db.session import Base, get_db
from app.models.agent_run import AgentRun
from app.models.card import Card
from app.models.draft import Draft
from app.models.topic import Topic
from app.schemas.draft_variant import DraftVariantGenerateRequest
from app.services import draft_variant_generator as variants
from app.services.workflow_support import WorkflowConflict


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAS_MODE", "false")
    engine = create_engine(f"sqlite:///{tmp_path / 'variant-review.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def client(database):
    app = FastAPI()
    app.include_router(router, prefix="/api")

    def session():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = session
    with TestClient(app) as test_client:
        yield test_client


def seed(database, *, run_status=None, use_rag=True):
    with database() as db:
        topic = Topic(title="合成资料整理流程", raw_summary="合成主题，描述资料核验和人工审核的流程。", status="generated")
        db.add(topic)
        db.flush()
        draft = Draft(topic_id=topic.id, status="draft", title_options=["合成草稿标题"],
                      cover_text_options=["合成封面"], body_text="合成正文：资料应先核验，再形成草稿，最后经过人工审核。",
                      hashtags=["合成示例"], fact_checks=[], risk_tips=[])
        db.add(draft)
        db.flush()
        card = Card(draft_id=draft.id, page_index=1, card_type="content", title="原卡片", body="原合成卡片内容")
        db.add(card)
        run = None
        if run_status:
            run = AgentRun(goal="合成审核任务", status=run_status, draft_id=draft.id,
                           selected_topic_id=topic.id, result_json={"_request": {"use_rag": use_rag},
                                                                  "review_history": [{"decision": "reject", "note": "合成历史"}]})
            db.add(run)
        db.commit()
        return draft.id, run.id if run else None


@pytest.mark.parametrize("status", ["pending", "running", "awaiting_review", "approved", "rejected", "failed", "cancelled", "completed"])
def test_every_workflow_status_blocks_variant_before_generation_or_writes(database, monkeypatch, status):
    draft_id, run_id = seed(database, run_status=status)
    local = Mock(side_effect=AssertionError("must not generate local content"))
    online = AsyncMock(side_effect=AssertionError("must not call an online model"))
    monkeypatch.setattr(variants, "_generate_local_variant", local)
    monkeypatch.setattr(variants, "_generate_llm_variant", online)
    with database() as db:
        original_body = db.get(Draft, draft_id).body_text
        original_result = deepcopy(db.get(AgentRun, run_id).result_json)
        for provider in ("local", "deepseek"):
            with pytest.raises(WorkflowConflict, match="返回原任务修改内容并重新提交审核"):
                asyncio.run(variants.generate_draft_variant(draft_id, DraftVariantGenerateRequest(provider=provider), db))
        assert db.query(Draft).count() == 1
        assert db.query(Card).count() == 1
        assert db.get(Draft, draft_id).body_text == original_body
        assert db.get(AgentRun, run_id).status == status
        assert db.get(AgentRun, run_id).result_json == original_result
    local.assert_not_called()
    online.assert_not_awaited()


@pytest.mark.parametrize("use_rag", [True, False])
def test_linked_variant_api_returns_actionable_409_without_changing_review(client, database, use_rag):
    draft_id, run_id = seed(database, run_status="approved", use_rag=use_rag)
    response = client.post(f"/api/drafts/{draft_id}/generate-variant", json={"provider": "local"})
    assert response.status_code == 409
    assert "原任务" in response.json()["detail"]
    assert "重新提交审核" in response.json()["detail"]
    with database() as db:
        assert db.query(Draft).count() == 1
        assert db.query(Card).count() == 1
        assert db.get(AgentRun, run_id).draft_id == draft_id
        assert db.get(AgentRun, run_id).status == "approved"


def test_plain_draft_still_generates_a_local_variant_and_cards(client, database):
    draft_id, _ = seed(database)
    response = client.post(f"/api/drafts/{draft_id}/generate-variant", json={"max_card_count": 3})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["draft"]["id"] != draft_id
    assert result["draft"]["status"] == "draft"
    assert result["draft"]["model_provider"] == "local"
    assert 2 <= len(result["cards"]) <= 3
    assert all(card["draft_id"] == result["draft"]["id"] for card in result["cards"])
    with database() as db:
        assert db.query(Draft).count() == 2
        assert db.query(AgentRun).count() == 0


def test_plain_draft_online_path_remains_available_with_synthetic_model(database, monkeypatch):
    draft_id, _ = seed(database)
    online = AsyncMock(return_value={})
    monkeypatch.setattr(variants, "_generate_llm_variant", online)
    with database() as db:
        variant, cards, _ = asyncio.run(variants.generate_draft_variant(
            draft_id, DraftVariantGenerateRequest(provider="deepseek", model="synthetic-model"), db))
        assert variant.id != draft_id and variant.model_provider == "deepseek"
        assert cards and db.query(AgentRun).count() == 0
    online.assert_awaited_once()


def test_another_workflow_on_same_topic_does_not_block_plain_draft(client, database):
    linked_id, _ = seed(database, run_status="approved")
    with database() as db:
        source = db.get(Draft, linked_id)
        plain = Draft(topic_id=source.topic_id, body_text="同选题的普通合成草稿", title_options=["普通草稿"])
        db.add(plain)
        db.commit()
        plain_id = plain.id
    assert client.post(f"/api/drafts/{plain_id}/generate-variant", json={}).status_code == 200


def test_missing_draft_still_returns_404(client):
    response = client.post("/api/drafts/999/generate-variant", json={})
    assert response.status_code == 404
    assert response.json()["detail"] == "草稿不存在"

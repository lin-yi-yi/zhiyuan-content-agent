"""Behavioral coverage for graph execution, atomic retry and human review.

All databases live under pytest's temporary directory; no user DB/API is used.
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # Register tables before create_all.
from app.api.routes.agent_runs import router
from app.db.session import Base, get_db
from app.models.agent_run import AgentRun, AgentStep
from app.models.card import Card
from app.models.draft import Draft
from app.models.topic import Topic
from app.schemas.agent_run import AgentRunCreate, AgentReviewCreate
from app.services import content_growth_agent as workflow
from app.services.workflow_support import CitationError, WorkflowConflict, validate_citations
from app.agent_core.rag_service import SearchHit
from app.llm.local import LocalRuleBasedClient


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(workflow, "SessionLocal", factory)
    monkeypatch.setattr(LocalRuleBasedClient, "_log_run", lambda *args, **kwargs: None)
    yield factory
    engine.dispose()


def create(factory, **kwargs):
    with factory() as db:
        return workflow.create_agent_run(AgentRunCreate(goal="AI Agent 知识工作流", provider="local", **kwargs), db).id


def read(factory, run_id):
    with factory() as db:
        return workflow.get_agent_run(run_id, db)


def hit(score=0.85, threshold=0.18):
    return SearchHit(chunk_id=7, document_id=3, knowledge_base_id=1, workspace_id=1,
        source_id=None, title="人工审核设计", source_uri="https://example.org/source",
        content="Agent 工作流在生成结果后进入人工审核，审核通过之后才交付草稿。失败任务可以从未完成步骤恢复，不应重复创建已保存的内容。",
        score=score, chunk_index=0, metadata={"retrieval_mode": "lexical", "evidence_threshold": threshold})


def mock_retrieval(monkeypatch, hits):
    monkeypatch.setattr(workflow, "search_knowledge", lambda **kwargs: hits)
    monkeypatch.setattr(workflow, "workspace_context", lambda *args: SimpleNamespace(workspace_id=1, workspace_slug="local"))
    monkeypatch.setattr(workflow, "get_knowledge_base_or_default", lambda *args: SimpleNamespace(id=1, name="测试资料"))


def test_graph_completes_only_to_review_and_records_duration(database):
    run_id = create(database)
    asyncio.run(workflow.execute_agent_run(run_id))
    run = read(database, run_id)
    assert run.status == "awaiting_review"
    assert run.draft.status == "awaiting_review"
    assert run.result_json["workflow"]["engine"] == "langgraph"
    assert next(step for step in run.steps if step.key == "generate_draft").duration_ms >= 0
    with database() as db:
        result = workflow.review_agent_run(run_id, AgentReviewCreate(decision="approve", note="已核对"), db)
        assert result.status == result.draft.status == "approved"
        assert result.result_json["review"]["publishes_content"] is False
        with pytest.raises(WorkflowConflict):
            workflow.review_agent_run(run_id, AgentReviewCreate(decision="reject"), db)
        with pytest.raises(WorkflowConflict):
            workflow.prepare_retry_agent_run(run_id, db)


def test_pending_cannot_be_reviewed_or_retried(database):
    run_id = create(database)
    with database() as db:
        with pytest.raises(WorkflowConflict):
            workflow.review_agent_run(run_id, AgentReviewCreate(decision="approve"), db)
        with pytest.raises(WorkflowConflict):
            workflow.prepare_retry_agent_run(run_id, db)
        assert workflow.cancel_agent_run(run_id, db).status == "cancelled"
    asyncio.run(workflow.execute_agent_run(run_id))
    assert read(database, run_id).draft is None


def test_rejection_records_a_decision_before_resubmission(database):
    run_id = create(database)
    asyncio.run(workflow.execute_agent_run(run_id))
    with database() as db:
        result = workflow.review_agent_run(run_id, AgentReviewCreate(decision="reject", note="事实需要补来源"), db)
        assert result.status == result.draft.status == "rejected"


def test_missing_evidence_stops_before_any_generation(database, monkeypatch):
    mock_retrieval(monkeypatch, [])
    run_id = create(database, use_rag=True)
    asyncio.run(workflow.execute_agent_run(run_id))
    run = read(database, run_id)
    assert run.status == "failed"
    assert run.result_json["failure"]["code"] == "INSUFFICIENT_EVIDENCE"
    assert run.result_json["rag_context"]["hits"] == []
    assert run.draft_id is None and run.selected_topic_id is None
    assert next(step for step in run.steps if step.key == "retrieve_context").status == "failed"


def test_retriever_threshold_is_honored(database, monkeypatch):
    mock_retrieval(monkeypatch, [hit(score=0.4, threshold=0.65)])
    run_id = create(database, use_rag=True)
    asyncio.run(workflow.execute_agent_run(run_id))
    assert read(database, run_id).result_json["failure"]["code"] == "INSUFFICIENT_EVIDENCE"


def test_rag_draft_and_cards_retain_real_source_citations(database, monkeypatch):
    mock_retrieval(monkeypatch, [hit()])
    run_id = create(database, use_rag=True)
    asyncio.run(workflow.execute_agent_run(run_id))
    run = read(database, run_id)
    assert run.status == "awaiting_review", run.error_message
    assert "[chunk:7]" in run.draft.body_text
    assert "本地摘录模式" in run.draft.body_text
    assert run.result_json["citations"][0]["source_uri"] == "https://example.org/source"
    assert any(card.highlight == "[chunk:7]" for card in run.cards)


def test_atomic_node_failure_and_retry_do_not_duplicate_artifacts(database, monkeypatch):
    original = workflow.generate_draft
    calls = 0

    async def fail_after_creating(*args, **kwargs):
        nonlocal calls
        calls += 1
        value = await original(*args, **kwargs)
        if calls == 1:
            raise RuntimeError("simulated process boundary after draft insertion")
        return value

    monkeypatch.setattr(workflow, "generate_draft", fail_after_creating)
    run_id = create(database)
    asyncio.run(workflow.execute_agent_run(run_id))
    first = read(database, run_id)
    assert first.status == "failed"
    with database() as db:
        assert db.query(Topic).count() == 1
        assert db.query(Draft).count() == 0  # The service's internal commit was buffered.
        retried = workflow.prepare_retry_agent_run(run_id, db)
        assert retried.status == "pending"
        with pytest.raises(WorkflowConflict):
            workflow.prepare_retry_agent_run(run_id, db)
    asyncio.run(workflow.execute_agent_run(run_id))
    second = read(database, run_id)
    assert second.status == "awaiting_review", second.error_message
    assert second.selected_topic_id == first.selected_topic_id
    assert second.result_json["workflow"]["attempt"] == 2
    with database() as db:
        assert db.query(Topic).count() == db.query(Draft).count() == 1
    asyncio.run(workflow.execute_agent_run(run_id))
    assert calls == 2  # Executing an already-finished run is a no-op.


def test_duplicate_background_dispatch_only_runs_once(database):
    run_id = create(database)

    async def dispatch_twice():
        await asyncio.gather(workflow.execute_agent_run(run_id), workflow.execute_agent_run(run_id))

    asyncio.run(dispatch_twice())
    with database() as db:
        assert db.query(Topic).count() == db.query(Draft).count() == 1
    assert read(database, run_id).status == "awaiting_review"


def test_cancellation_during_node_preserves_cancelled_state(database, monkeypatch):
    original = workflow.generate_custom_topic_ideas

    async def scenario():
        entered, resume = asyncio.Event(), asyncio.Event()

        async def paused(*args, **kwargs):
            entered.set()
            await resume.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(workflow, "generate_custom_topic_ideas", paused)
        run_id = create(database)
        task = asyncio.create_task(workflow.execute_agent_run(run_id))
        await entered.wait()
        with database() as db:
            workflow.cancel_agent_run(run_id, db)
        resume.set()
        await task
        return run_id

    run_id = asyncio.run(scenario())
    run = read(database, run_id)
    assert run.status == "cancelled"
    assert run.draft_id is None
    assert all(step.status != "running" for step in run.steps)


def test_restart_marks_interrupted_work_and_explicit_retry_recovers(database):
    run_id = create(database)
    with database() as db:
        run = db.get(AgentRun, run_id)
        run.status = "running"
        step = db.query(AgentStep).filter_by(run_id=run_id, key="retrieve_context").one()
        workflow._start_step(run, step, {}, db)
        assert workflow.recover_interrupted_agent_runs(db) == 1
        assert workflow.recover_interrupted_agent_runs(db) == 0
        assert workflow.get_agent_run(run_id, db).result_json["failure"]["code"] == "INTERRUPTED"
        workflow.prepare_retry_agent_run(run_id, db)
    asyncio.run(workflow.execute_agent_run(run_id))
    assert read(database, run_id).status == "awaiting_review"


def test_quality_branch_revises_once(database, monkeypatch):
    calls = 0

    async def score(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"overall_score": 50 if calls == 1 else 80, "publish_readiness": "needs_review"}

    monkeypatch.setattr(workflow, "evaluate_draft", score)
    run_id = create(database)
    asyncio.run(workflow.execute_agent_run(run_id))
    run = read(database, run_id)
    assert run.status == "awaiting_review"
    assert calls == 2
    assert next(step for step in run.steps if step.key == "revise_package").status == "completed"


def test_unknown_citations_rejected():
    with pytest.raises(CitationError):
        validate_citations("claims [chunk:404]", [{"chunk_id": 7}])
    with pytest.raises(CitationError):
        validate_citations("claims without references", [{"chunk_id": 7}])


def test_http_state_conflicts_are_409(database):
    app = FastAPI()
    app.include_router(router, prefix="/api")

    def session():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = session
    run_id = create(database)
    with TestClient(app) as client:
        assert client.post(f"/api/agent-runs/{run_id}/review", json={"decision": "approve"}).status_code == 409
        assert client.post(f"/api/agent-runs/{run_id}/retry").status_code == 409
        assert client.post(f"/api/agent-runs/{run_id}/cancel").status_code == 200
        assert client.post(f"/api/agent-runs/{run_id}/retry").status_code == 409
        assert client.post(f"/api/agent-runs/{run_id}/review", json={"decision": "publish"}).status_code == 422
        assert client.post("/api/agent-runs/99999/cancel").status_code == 404


def test_cloud_draft_receives_evidence_and_validates_citations(database, monkeypatch):
    from app.services import workflow_support
    from app.services.custom_topic_creator import CustomTopicIdea
    captured = {}

    class Client:
        model = "test-only-fake"

        def chat_json(self, system, user, temperature):
            captured.update(system=system, user=user)
            return {"title_options": ["审核流程"], "body_text": "生成后进入人工审核。[chunk:7]"}

    monkeypatch.setattr(workflow_support, "llm_router", SimpleNamespace(get_task_client=lambda *args, **kwargs: Client()))
    with database() as db:
        topic = workflow._create_topic_from_idea(CustomTopicIdea(title="审核", content_angle="教程", target_audience="开发者",
            summary="审核流程", reason="用于测试", risk_tip="待核验"), db)
        draft, citations = asyncio.run(workflow_support.generate_evidence_draft(
            topic, AgentRunCreate(goal="审核流程", provider="test-cloud"), [hit().to_dict()], db,
        ))
        assert draft.model_name == "test-only-fake"
        assert citations[0]["chunk_id"] == 7
        assert "Agent 工作流在生成结果后进入人工审核" in captured["user"]
        assert "不可信数据" in captured["system"]
        db.rollback()
        assert db.query(Draft).count() == 0


@pytest.fixture
def api_client(database):
    from app.api.routes.drafts import router as draft_router
    from app.api.routes.cards import router as card_router
    app = FastAPI()
    for feature in (router, draft_router, card_router):
        app.include_router(feature, prefix="/api")

    def session():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = session
    with TestClient(app) as client:
        yield client


def completed_run(database, decision="approve"):
    run_id = create(database)
    asyncio.run(workflow.execute_agent_run(run_id))
    with database() as db:
        workflow.review_agent_run(run_id, AgentReviewCreate(decision=decision, note="测试审核备注"), db)
    return read(database, run_id)


def test_rejected_edit_and_resubmit_preserve_audit_history(database, api_client):
    run = completed_run(database, "reject")
    response = api_client.put(f"/api/drafts/{run.draft_id}", json={"body_text": "已根据审核意见修订的草稿"})
    assert response.status_code == 200
    changed = read(database, run.id)
    assert changed.status == "rejected"
    assert "review" not in changed.result_json
    assert changed.result_json["review_history"][0]["decision"] == "reject"
    response = api_client.post(f"/api/agent-runs/{run.id}/submit-review")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_review"
    assert api_client.post(f"/api/agent-runs/{run.id}/submit-review").status_code == 409
    assert api_client.post(f"/api/agent-runs/{run.id}/review", json={"decision": "approve"}).status_code == 200
    final = read(database, run.id)
    assert final.status == "approved"
    assert len(final.result_json["review_history"]) == 1
    assert len(final.result_json["review"]["content_hash"]) == 64


def test_approved_draft_edit_revokes_approval_and_stale_score(database, api_client):
    run = completed_run(database)
    before_hash = run.result_json["review"]["content_hash"]
    response = api_client.put(f"/api/drafts/{run.draft_id}", json={"body_text": "新的事实陈述需要重新核验"})
    assert response.status_code == 200
    updated = read(database, run.id)
    assert updated.status == updated.draft.status == "awaiting_review"
    assert "review" not in updated.result_json
    assert updated.result_json["review_history"][0]["content_hash"] == before_hash
    previous = updated.result_json["content_history"][0]
    assert previous["content_hash"] == before_hash and previous["revision"] == 1
    assert previous["snapshot"]["draft"]["body_text"] == run.draft.body_text
    assert previous["actor"] == "local_user" and previous["at"] and previous["reason"]
    assert updated.result_json["evaluation_stale"] is True
    assert updated.evaluation is None and updated.evaluation_score is None
    assert api_client.post(f"/api/agent-runs/{run.id}/review", json={"decision": "approve"}).status_code == 200
    assert read(database, run.id).result_json["review"]["content_hash"] != before_hash


@pytest.mark.parametrize("operation", ["update", "create", "duplicate", "split", "move", "delete", "batch-style"])
def test_every_card_mutation_revokes_approval(database, api_client, operation):
    run = completed_run(database)
    card_id = run.cards[0].id
    if operation == "update":
        response = api_client.put(f"/api/cards/{card_id}", json={"body": "修改后的卡片"})
    elif operation == "create":
        response = api_client.post(f"/api/cards/draft/{run.draft_id}", json={"title": "补充证据"})
    elif operation in {"duplicate", "split"}:
        response = api_client.post(f"/api/cards/{card_id}/{operation}")
    elif operation == "move":
        response = api_client.put(f"/api/cards/{card_id}/move", json={"direction": "down"})
    elif operation == "delete":
        response = api_client.delete(f"/api/cards/{card_id}")
    else:
        response = api_client.put(f"/api/cards/draft/{run.draft_id}/batch-style", json={"theme_key": "changed-theme"})
    assert response.status_code == 200, response.text
    updated = read(database, run.id)
    assert updated.status == updated.draft.status == "awaiting_review"
    assert updated.result_json["review_history"][0]["decision"] == "approve"
    assert "review" not in updated.result_json
    old_cards = updated.result_json["content_history"][0]["snapshot"]["cards"]
    assert [card["id"] for card in old_cards] == [card.id for card in run.cards]
    assert old_cards[0]["body"] == run.cards[0].body
    assert old_cards[0]["title"] == run.cards[0].title
    assert old_cards[0]["theme_key"] == run.cards[0].theme_key


def test_direct_draft_status_cannot_bypass_review(database, api_client):
    run_id = create(database)
    asyncio.run(workflow.execute_agent_run(run_id))
    run = read(database, run_id)
    response = api_client.put(f"/api/drafts/{run.draft_id}", json={"status": "approved"})
    assert response.status_code == 409
    assert read(database, run_id).status == "awaiting_review"


def test_deleting_approved_draft_retains_history_and_cancels_run(database, api_client):
    run = completed_run(database)
    response = api_client.delete(f"/api/drafts/{run.draft_id}")
    assert response.status_code == 204, response.text
    updated = read(database, run.id)
    assert updated.status == "cancelled" and updated.draft_id is None
    assert updated.result_json["review_history"][0]["decision"] == "approve"
    assert updated.result_json["content_history"][0]["snapshot"]["draft"]["body_text"] == run.draft.body_text


def test_each_actual_edit_keeps_reconstructable_content_without_noop_duplicates(database, api_client):
    run = completed_run(database, "reject")
    route = f"/api/drafts/{run.draft_id}"
    for text in ("第一次合成修订", "第一次合成修订", "第二次合成修订"):
        assert api_client.put(route, json={"body_text": text}).status_code == 200
    updated = read(database, run.id)
    history = updated.result_json["content_history"]
    assert [item["revision"] for item in history] == [1, 2]
    assert [item["snapshot"]["draft"]["body_text"] for item in history] == [run.draft.body_text, "第一次合成修订"]
    assert updated.draft.body_text == "第二次合成修订" and updated.result_json["content_revision"] == 3
    assert len(updated.result_json["review_history"]) == 1
    assert all("_request" not in item["snapshot"] for item in history)


@pytest.mark.parametrize("operation", ["edit", "delete"])
def test_history_refreshes_content_loaded_before_another_editor_committed(database, operation):
    from app.services.review_lifecycle import invalidate_review_for_delete, invalidate_review_for_edit

    run = completed_run(database)
    with database() as stale_db:
        stale_draft = stale_db.get(Draft, run.draft_id)
        stale_card = stale_db.get(Card, run.cards[0].id)
        with database() as other_db:
            current_draft = other_db.get(Draft, run.draft_id)
            invalidate_review_for_edit(current_draft, other_db, "第一位编辑的合成修改")
            current_draft.body_text = "第一位编辑已提交的正文"
            other_db.get(Card, stale_card.id).body = "第一位编辑已提交的卡片"
            other_db.commit()
        assert stale_draft.body_text != "第一位编辑已提交的正文"
        if operation == "edit":
            invalidate_review_for_edit(stale_draft, stale_db, "第二位编辑修改标题")
            stale_draft.selected_title = "第二位编辑标题"
        else:
            invalidate_review_for_delete(stale_draft, stale_db)
            stale_db.delete(stale_draft)
        stale_db.commit()
    history = read(database, run.id).result_json["content_history"]
    assert len(history) == 2
    assert history[-1]["snapshot"]["draft"]["body_text"] == "第一位编辑已提交的正文"
    assert history[-1]["snapshot"]["cards"][0]["body"] == "第一位编辑已提交的卡片"


def test_resubmit_checks_citation_presence_and_current_source(database, api_client, monkeypatch):
    mock_retrieval(monkeypatch, [hit()])
    run_id = create(database, use_rag=True)
    asyncio.run(workflow.execute_agent_run(run_id))
    run = read(database, run_id)
    assert api_client.post(f"/api/agent-runs/{run_id}/review", json={"decision": "reject"}).status_code == 200
    # The retrieval stub's source IDs do not exist: a citation-shaped string alone is not valid.
    response = api_client.post(f"/api/agent-runs/{run_id}/submit-review")
    assert response.status_code == 409 and "引用资料" in response.json()["detail"]
    assert api_client.put(f"/api/drafts/{run.draft_id}", json={"body_text": "已移除全部引用"}).status_code == 200
    response = api_client.post(f"/api/agent-runs/{run_id}/submit-review")
    assert response.status_code == 409 and "引用" in response.json()["detail"]
    assert read(database, run_id).status == "rejected"


def test_polling_and_cancel_remain_responsive_during_blocking_provider(database, monkeypatch):
    """Real HTTP server: a synchronous mock model blocks its worker, not ASGI."""
    import socket
    import threading
    import time
    import httpx
    import uvicorn

    original = workflow.generate_custom_topic_ideas
    entered = threading.Event()
    released = threading.Event()

    async def blocking_provider(*args, **kwargs):
        entered.set()
        released.wait(timeout=4)  # Deliberately block this async function like a sync SDK.
        return await original(*args, **kwargs)

    monkeypatch.setattr(workflow, "generate_custom_topic_ideas", blocking_provider)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    def session():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = session

    @app.get("/probe")
    async def probe():
        return {"event_loop": "responsive"}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1.5) as client:
            created = client.post("/api/agent-runs", json={"goal": "响应性测试", "provider": "local"})
            assert created.status_code == 201
            run_id = created.json()["id"]
            assert entered.wait(2)
            started = time.monotonic()
            assert client.get("/probe").status_code == 200
            assert client.get(f"/api/agent-runs/{run_id}").json()["status"] == "running"
            cancelled = client.post(f"/api/agent-runs/{run_id}/cancel")
            assert cancelled.status_code == 200
            assert time.monotonic() - started < 1.5
            assert not released.is_set()  # Responses arrived while the provider was still blocked.
            released.set()
    finally:
        released.set()
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
    assert not thread.is_alive()
    assert read(database, run_id).status == "cancelled"


def test_review_accepts_live_index_then_rejects_replaced_source(database, api_client, monkeypatch):
    from app.agent_core.boundaries import workspace_context
    from app.agent_core.rag_service import index_source
    from app.models.source import Source
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    with database() as db:
        context = workspace_context(db)
        source = Source(title="人工审核规范", source_type="markdown", raw_content=(
            "人工审核规范。内容草稿生成后必须进入人工审核。审核人核对引用来源与事实陈述。"
            "人工审核通过后仅表示草稿可交付，并不自动发布到外部平台。资料不足时停止生成并补充证据。"))
        db.add(source)
        db.commit()
        source_id = source.id
        index_source(source_id, db, context)
    with database() as db:
        run_id = workflow.create_agent_run(AgentRunCreate(goal="人工审核规范", use_rag=True, provider="local"), db).id
    asyncio.run(workflow.execute_agent_run(run_id))
    run = read(database, run_id)
    assert run.status == "awaiting_review", run.error_message
    assert api_client.post(f"/api/agent-runs/{run_id}/review", json={"decision": "approve"}).status_code == 200
    # An edit invalidates the approval, and live-source validation protects its next review.
    assert api_client.put(f"/api/drafts/{run.draft_id}", json={"body_text": run.draft.body_text + "\n补充待核验说明。"}).status_code == 200
    with database() as db:
        source = db.get(Source, source_id)
        source.raw_content += "\n来源规范现已修订，应以新的资料片段为准；旧的引用需要重新生成后再审核。"
        index_source(source_id, db, workspace_context(db))
    response = api_client.post(f"/api/agent-runs/{run_id}/review", json={"decision": "approve"})
    assert response.status_code == 409 and "引用资料" in response.json()["detail"]
    assert read(database, run_id).status == "awaiting_review"


@pytest.mark.parametrize("kind", ["runtime", "sql_statement"])
def test_unknown_failure_does_not_persist_or_return_sensitive_text(database, api_client, monkeypatch, kind):
    import json
    from sqlalchemy.exc import StatementError
    secret = "SECRET-MUST-NOT-BE-PERSISTED-8fe472"
    error = (RuntimeError(f"remote payload contains {secret}") if kind == "runtime" else
             StatementError("SQL parameter failure", "INSERT sensitive VALUES (:payload)",
                            {"payload": secret}, ValueError(secret)))

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(workflow, "generate_custom_topic_ideas", fail)
    run_id = create(database)
    asyncio.run(workflow.execute_agent_run(run_id))
    with database() as db:
        persisted_run = db.get(AgentRun, run_id)
        persisted_steps = db.query(AgentStep).filter(AgentStep.run_id == run_id).all()
        stored = json.dumps({"run_error": persisted_run.error_message, "result": persisted_run.result_json,
            "steps": [{"error": step.error_message, "input": step.input_json, "output": step.output_json}
                      for step in persisted_steps]}, ensure_ascii=False)
        assert secret not in stored
        assert persisted_run.result_json["failure"]["error_type"] == type(error).__name__
        assert persisted_run.result_json["failure"]["code"] == "STEP_FAILED"
    response = api_client.get(f"/api/agent-runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert secret not in response.text


def test_evidence_card_shortening_keeps_complete_sentences_and_citations(database, monkeypatch):
    from dataclasses import replace
    from app.services.package_evaluator import evaluate_local
    from app.services.workflow_support import complete_sentence_excerpt

    sentence = "需要由使用者在平台上完成发布，并手动登记发布时间、来源链接和审核结论。"
    source_text = sentence * 10
    hits = [replace(hit(), chunk_id=index, content=source_text) for index in (7, 8, 9)]
    mock_retrieval(monkeypatch, hits)

    async def low_score(*args, **kwargs):
        return {"overall_score": 50, "publish_readiness": "needs_review"}

    monkeypatch.setattr(workflow, "evaluate_draft", low_score)
    run_id = create(database, use_rag=True)
    asyncio.run(workflow.execute_agent_run(run_id))
    run = read(database, run_id)
    assert run.status == "awaiting_review", run.error_message
    assert len(run.cards) == 7
    for card in run.cards[1:]:
        assert card.body.endswith("。") and card.body in source_text
        assert len(card.body) < len(source_text)
        assert card.highlight == card.subtitle and card.highlight.startswith("[chunk:")
    # Shortening must not lose a source marker attached at the end of a card.
    assert "[chunk:7]" in complete_sentence_excerpt(source_text + " [chunk:7]", 138)
    assert complete_sentence_excerpt("这一整句" * 60 + "。", 138).endswith("。")
    with database() as db:
        draft = db.get(Draft, run.draft_id)
        cards = db.query(Card).filter(Card.draft_id == run.draft_id).all()
        result = evaluate_local(draft, cards)
        assert result["scores"]["card_rhythm"] == 8
        assert not any("页" in issue.get("message", "") and "补到" in issue["message"] for issue in result["issues"])


def test_evidence_revision_preserves_title_and_cover_meaning(database):
    with database() as db:
        topic = Topic(title="合成标题", score=0)
        db.add(topic)
        db.flush()
        draft = Draft(topic_id=topic.id, body_text="保存修改后返回原任务重新提交。[chunk:7]",
                      title_options=["修改后如何重新提交"],
                      cover_text_options=["从原任务打开稿件，保存修改后重新提交审核"])
        db.add(draft)
        db.commit()
        workflow._revise_draft_and_cards(draft, [], {"overall_score": 50}, db)
        assert draft.title_options == ["修改后如何重新提交"]
        assert draft.cover_text_options == ["从原任务打开稿件，保存修改后重新提交审核"]

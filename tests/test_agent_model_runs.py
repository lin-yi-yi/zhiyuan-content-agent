"""Node/call attribution and safe metadata APIs using isolated SQLite and mock SDKs.

No model API, account, billing service or production database is contacted.
"""
import asyncio
from decimal import Decimal
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
from app.api.routes.agent_runs import router as agent_router
from app.api.routes.models import router as models_router
from app.db import session as db_module
from app.db.session import Base, get_db
from app.llm.local import LocalRuleBasedClient
from app.models.agent_run import AgentRun, AgentStep
from app.models.draft import Draft
from app.models.model_run import ModelRun
from app.schemas.agent_run import AgentRunCreate
from app.services import content_growth_agent as workflow

from test_model_client import client_with_results, completion
from test_saas_isolation import application, teams, invited_member, business_db


@pytest.fixture
def trace_database(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAS_MODE", "false")
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    engine = create_engine(f"sqlite:///{tmp_path / 'trace.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(workflow, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    yield factory
    engine.dispose()


@pytest.fixture
def trace_api(trace_database):
    app = FastAPI()
    app.include_router(agent_router, prefix="/api")
    app.include_router(models_router, prefix="/api")

    def sessions():
        with trace_database() as db:
            yield db

    app.dependency_overrides[get_db] = sessions
    with TestClient(app) as client:
        yield client


def create_run(factory):
    with factory() as db:
        return workflow.create_agent_run(AgentRunCreate(goal="合成任务调用追踪", provider="local", auto_score=False), db).id


def seed_run(db, *, generation_mode="llm"):
    run = AgentRun(goal="合成元数据查询", status="failed", provider="local",
                   result_json={"workflow": {"generation_mode": generation_mode}})
    db.add(run)
    db.flush()
    return run


def record(run_id, **changes):
    values = {
        "agent_run_id": run_id, "task_type": "chat", "provider": "test", "model_name": "mock-model",
        "workflow_attempt": 1, "step_key": "generate_draft", "call_kind": "initial", "request_index": 1,
        "prompt_hash": "a" * 64, "prompt_version": "b" * 64, "success": True,
        "prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22,
        "estimated_cost": Decimal("0.000123"), "cost_currency": "CNY", "pricing_version": "synthetic-v1",
        "cost_status": "estimated",
    }
    values.update(changes)
    return ModelRun(**values)


def test_real_local_graph_associates_rule_logs_without_external_requests(trace_database, trace_api):
    run_id = create_run(trace_database)
    asyncio.run(workflow.execute_agent_run(run_id))
    response = trace_api.get(f"/api/agent-runs/{run_id}/model-runs")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["generation_mode"] == "local_rules"
    assert payload["total"] > 0 and len(payload["runs"]) == payload["total"]
    with trace_database() as db:
        assert db.get(AgentRun, run_id).status == "awaiting_review"
        steps = {row.id: row for row in db.query(AgentStep).filter_by(run_id=run_id)}
    for row in payload["runs"]:
        assert row["agent_run_id"] == run_id and row["workflow_attempt"] == 1
        assert row["step_key"] == steps[row["agent_step_id"]].key
        assert row["call_kind"] == "local_rule" and row["provider"] == "local"
        assert row["invocation_id"] and row["request_index"] == 1
        assert len(row["prompt_version"]) == len(row["prompt_hash"]) == 64
        assert row["total_tokens"] is None and row["estimated_cost"] is None
    summary = payload["summary"]
    assert summary["recorded_external_request_count"] == 0
    assert summary["local_rule_call_count"] == payload["total"]
    assert summary["total_tokens"] is None and summary["estimated_cost"] is None
    assert summary["cost_status"] == "no_external_requests" and summary["provider_invoice_amount"] is None


@pytest.mark.parametrize("failure", ["provider", "after_artifact"])
def test_failed_node_retains_independent_logs_and_retry_uses_new_attempt(trace_database, trace_api, monkeypatch, failure):
    first = RuntimeError("provider secret payload") if failure == "provider" else completion("private output")
    mock_client, requests = client_with_results([first, completion("private output")])
    original = workflow._step_generate_draft
    executions = 0

    async def injected(run, req, topic, step, db):
        nonlocal executions
        executions += 1
        mock_client.chat("PRIVATE_SYSTEM_SECRET", "PRIVATE_USER_SECRET")
        if executions == 1:
            db.add(Draft(topic_id=topic.id, body_text="synthetic partial artifact"))
            db.flush()
            raise RuntimeError("synthetic rollback after artifact flush")
        return await original(run, req, topic, step, db)

    monkeypatch.setattr(workflow, "_step_generate_draft", injected)
    run_id = create_run(trace_database)
    asyncio.run(workflow.execute_agent_run(run_id))
    with trace_database() as db:
        assert db.get(AgentRun, run_id).status == "failed"
        assert db.query(Draft).count() == 0
        failed = db.query(ModelRun).filter_by(agent_run_id=run_id, provider="test").one()
        assert failed.workflow_attempt == 1 and failed.step_key == "generate_draft"
        assert failed.success is (failure != "provider")
        workflow.prepare_retry_agent_run(run_id, db)
    asyncio.run(workflow.execute_agent_run(run_id))
    with trace_database() as db:
        assert db.get(AgentRun, run_id).status == "awaiting_review"
        assert db.query(Draft).count() == 1
        rows = db.query(ModelRun).filter_by(agent_run_id=run_id, provider="test").order_by(ModelRun.id).all()
        assert [row.workflow_attempt for row in rows] == [1, 2]
        assert len({row.agent_step_id for row in rows}) == 1
        assert len({row.invocation_id for row in rows}) == 2 and len(requests) == 2
        assert all(row.input_preview is None and row.output_preview is None and row.error_message is None for row in rows)
    payload = trace_api.get(f"/api/agent-runs/{run_id}/model-runs").json()
    assert payload["summary"]["recorded_external_request_count"] == 2
    assert payload["summary"]["estimated_cost"] is None  # No configured mock-provider prices.
    assert "PRIVATE_" not in str(payload) and "provider secret" not in str(payload)
    if failure == "provider":
        assert payload["summary"]["unknown_usage_call_count"] == 1
        assert payload["summary"]["total_tokens"] is None
    # Once the graph exits, an unrelated call must not inherit its run identity.
    LocalRuleBasedClient().chat("unrelated system", "unrelated user")
    with trace_database() as db:
        assert db.query(ModelRun).order_by(ModelRun.id.desc()).first().agent_run_id is None


def test_paginated_task_metadata_excludes_legacy_rows_and_totals_all_recorded_pages(trace_database, trace_api):
    with trace_database() as db:
        run = seed_run(db)
        run_id = run.id
        db.add_all([record(run_id), record(run_id), record(None, input_preview="LEGACY_PRIVATE_INPUT",
                    output_preview="LEGACY_PRIVATE_OUTPUT", error_message="LEGACY_PRIVATE_ERROR")])
        db.commit()
    first = trace_api.get(f"/api/agent-runs/{run_id}/model-runs", params={"limit": 1}).json()
    assert first["total"] == 2 and first["has_more"] and len(first["runs"]) == 1
    assert first["summary"]["recorded_call_count"] == first["summary"]["recorded_external_request_count"] == 2
    assert first["summary"]["total_tokens"] == 44
    assert Decimal(first["summary"]["estimated_cost"]) == Decimal("0.000246")
    assert first["summary"]["cost_currency"] == "CNY" and first["summary"]["cost_status"] == "complete_estimate"
    second = trace_api.get(f"/api/agent-runs/{run_id}/model-runs", params={"limit": 1, "offset": 1}).json()
    assert not second["has_more"] and first["runs"][0]["id"] != second["runs"][0]["id"]
    assert second["summary"] == first["summary"]
    all_rows = trace_api.get("/api/models/runs").json()["runs"]
    assert len(all_rows) == 3 and any(row["agent_run_id"] is None for row in all_rows)
    assert "LEGACY_PRIVATE" not in str(all_rows)


@pytest.mark.parametrize("missing", ["usage", "price", "failed", "mixed_currency"])
def test_summary_never_presents_partial_estimates_as_the_total(trace_database, trace_api, missing):
    with trace_database() as db:
        run = seed_run(db)
        run_id = run.id
        extra = {"estimated_cost": None, "cost_status": "unpriced"}
        if missing == "usage":
            extra.update(prompt_tokens=None, completion_tokens=None, total_tokens=None, cost_status="missing_usage")
        elif missing == "failed":
            extra.update(success=False, cost_status="failed")
        elif missing == "mixed_currency":
            extra = {"cost_currency": "USD"}
        db.add_all([record(run_id), record(run_id, **extra)])
        db.commit()
    summary = trace_api.get(f"/api/agent-runs/{run_id}/model-runs").json()["summary"]
    assert summary["estimated_cost"] is None and summary["cost_currency"] is None
    assert summary["cost_status"] == ("mixed_currency" if missing == "mixed_currency" else "incomplete")
    assert summary["total_tokens"] == (None if missing == "usage" else 44)
    assert summary["provider_invoice_amount"] is None


def test_missing_run_and_invalid_pagination_do_not_infer_history(trace_database, trace_api):
    with trace_database() as db:
        db.add(record(None))
        db.commit()
    assert trace_api.get("/api/agent-runs/999999/model-runs").status_code == 404
    assert trace_api.get("/api/agent-runs/999999/model-runs?limit=501").status_code == 422
    assert trace_api.get("/api/agent-runs/999999/model-runs?offset=-1").status_code == 422


@pytest.mark.parametrize("role", ["viewer", "editor", "reviewer"])
def test_task_model_metadata_requires_login_and_stays_in_the_selected_organization(application, teams, role):
    owner, other = teams
    ids = []
    for identity, marker in ((owner, "ALPHA"), (other, "BETA")):
        with business_db(identity) as db:
            run = seed_run(db)
            ids.append(run.id)
            db.add(record(run.id, model_name=f"{marker}-MODEL", input_preview=f"{marker}-PRIVATE",
                          output_preview=f"{marker}-PRIVATE", error_message=f"{marker}-PRIVATE"))
            db.commit()
    assert ids[0] == ids[1]
    route = f"/api/agent-runs/{ids[0]}/model-runs"
    assert application.anonymous.get(route).status_code == 401
    member = invited_member(application, owner, role)
    for client in (owner.client, member.client):
        response = client.get(route)
        assert response.status_code == 200, response.text
        assert response.json()["runs"][0]["model_name"] == "ALPHA-MODEL"
        assert "BETA" not in response.text and "PRIVATE" not in response.text
    assert other.client.get(route).json()["runs"][0]["model_name"] == "BETA-MODEL"
    assert member.client.get(route, headers={"X-Organization-ID": other.org}).status_code == 403

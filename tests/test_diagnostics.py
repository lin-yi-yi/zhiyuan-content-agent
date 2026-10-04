"""Request/workflow correlation with temporary stores and synthetic failures only."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import agent_runs, health
from app.core import diagnostics
from app.db.session import get_db
from app.llm.local import LocalRuleBasedClient
from app.models.agent_run import AgentRun
from app.models.draft import Draft
from app.models.model_run import ModelRun
from app.saas.context import TenantContext, current_tenant
from app.services import content_growth_agent as workflow

from test_agent_model_runs import trace_database
from test_saas_isolation import application, teams, business_db


SECRET = "SYNTHETIC-PRIVATE-790acf"
FORGED_ID = "f" * 32


@pytest.fixture
def events(caplog):
    diagnostics.LOGGER.addHandler(caplog.handler)
    diagnostics.LOGGER.setLevel(logging.INFO)
    try:
        yield lambda: [json.loads(record.getMessage()) for record in caplog.records
                       if record.name == diagnostics.LOGGER.name]
    finally:
        diagnostics.LOGGER.removeHandler(caplog.handler)


@pytest.fixture
def local_api(trace_database):
    app = FastAPI()
    app.add_middleware(diagnostics.RequestDiagnosticsMiddleware)
    app.include_router(agent_runs.router, prefix="/api")
    app.include_router(health.router)

    def sessions():
        with trace_database() as db:
            yield db
    app.dependency_overrides[get_db] = sessions

    @app.post("/synthetic-failure/{private_path}")
    async def failure(private_path: str):
        # Even dynamically generated exception class names must remain private.
        raise type(SECRET, (Exception,), {})(SECRET)

    with TestClient(app) as client:
        yield client


def request_id(response):
    value = response.headers["X-Request-ID"]
    assert re.fullmatch("[a-f0-9]{32}", value) and value != FORGED_ID
    assert response.headers["Server-Timing"].startswith("app;dur=")
    return value


def assert_private_events(events):
    encoded = json.dumps(events(), ensure_ascii=False)
    assert SECRET not in encoded
    assert "Traceback" not in encoded
    assert FORGED_ID not in encoded


def test_http_500_is_correlated_without_body_query_path_or_exception_name(local_api, events):
    response = local_api.post(f"/synthetic-failure/{SECRET}?token={SECRET}",
                              headers={"X-Request-ID": FORGED_ID, "Authorization": SECRET},
                              json={"prompt": SECRET, "document": SECRET})
    assert response.status_code == 500
    correlation = request_id(response)
    assert response.json()["request_id"] == correlation and SECRET not in response.text
    records = events()
    assert [record["event"] for record in records] == ["http_failed", "http_response"]
    assert all(record["request_id"] == correlation for record in records)
    assert all(record["route"] == "/synthetic-failure/{private_path}" for record in records)
    assert records[0]["error_type"] == "UnexpectedError"
    assert records[1]["http_status"] == 500
    assert diagnostics.current_request_id.get() is None
    assert_private_events(events)


def test_readiness_503_and_normal_health_have_separate_ids(local_api, monkeypatch, events):
    def unavailable():
        raise OSError(SECRET)
    monkeypatch.setattr(health, "SessionLocal", unavailable)
    failed = local_api.get(f"/ready?path={SECRET}")
    healthy = local_api.get("/health", headers={"X-Request-ID": FORGED_ID})
    assert failed.status_code == 503 and healthy.status_code == 200
    assert request_id(failed) != request_id(healthy)
    assert [record["http_status"] for record in events()] == [503, 200]
    assert_private_events(events)


def test_saas_early_401_403_and_503_include_ids_without_trusting_headers(application, monkeypatch, events):
    unauthorized = application.anonymous.get(f"/api/{SECRET}?token={SECRET}",
        headers={"X-Request-ID": FORGED_ID, "X-Organization-ID": "e" * 32})
    forbidden = application.anonymous.post("/api/agent-runs", headers={"Origin": "https://example.invalid"},
        json={"goal": SECRET})
    from app.saas import middleware
    def broken_origin():
        raise type(SECRET, (Exception,), {})(SECRET)
    monkeypatch.setattr(middleware, "public_origin", broken_origin)
    unavailable = application.anonymous.get(f"/api/{SECRET}")
    assert [item.status_code for item in (unauthorized, forbidden, unavailable)] == [401, 403, 503]
    ids = {request_id(item) for item in (unauthorized, forbidden, unavailable)}
    assert len(ids) == 3
    records = events()
    assert {record["request_id"] for record in records} == ids
    assert all(record["organization_id"] is None for record in records)
    assert all(record["route"] in {None, "<unmatched>"} for record in records)
    assert any(record["event"] == "saas_boundary_failed" and record["error_type"] == "UnexpectedError"
               for record in records)
    assert_private_events(events)


def test_saas_business_exception_stays_500_and_keeps_authenticated_context(application, teams, monkeypatch, events):
    def failed(*args, **kwargs):
        raise type(SECRET, (Exception,), {})(SECRET)
    monkeypatch.setattr(LocalRuleBasedClient, "chat", failed)
    team = teams[0]
    response = team.client.post("/api/models/chat", json={"provider": "local", "user_prompt": SECRET})
    assert response.status_code == 500
    correlation = request_id(response)
    assert response.json()["request_id"] == correlation
    records = [record for record in events() if record["request_id"] == correlation]
    assert [record["event"] for record in records] == ["http_failed", "http_response"]
    assert all(record["organization_id"] == team.org for record in records)
    assert_private_events(events)


@pytest.mark.parametrize("failure", ["exception", "transaction"])
def test_failed_background_attempt_and_retry_keep_ids_and_model_linkage(local_api, trace_database, monkeypatch, events, failure):
    original = workflow._step_generate_draft
    calls = 0
    async def fail_first(run, req, topic, step, db):
        nonlocal calls
        calls += 1
        # Real local log persistence inside the graph node's model context.
        LocalRuleBasedClient().chat(SECRET, SECRET)
        if calls == 1:
            if failure == "transaction":
                db.add_all([Draft(id=991, topic_id=topic.id, body_text=SECRET),
                            Draft(id=991, topic_id=topic.id, body_text=SECRET)])
                db.flush()  # SQL failure expires ORM objects before diagnostics.
            raise type(SECRET, (Exception,), {})(SECRET)
        return await original(run, req, topic, step, db)
    monkeypatch.setattr(workflow, "_step_generate_draft", fail_first)
    created = local_api.post("/api/agent-runs", json={"goal": SECRET, "provider": "local", "auto_score": False},
                             headers={"X-Request-ID": FORGED_ID})
    assert created.status_code == 201
    first_id, run_id = request_id(created), created.json()["id"]
    assert created.json()["result_json"]["workflow"]["request_id"] == first_id
    failed = local_api.get(f"/api/agent-runs/{run_id}").json()
    assert failed["status"] == "failed"
    assert failed["result_json"]["workflow"]["request_id"] == first_id
    assert SECRET not in failed["error_message"]
    retried = local_api.post(f"/api/agent-runs/{run_id}/retry")
    assert retried.status_code == 200
    second_id = request_id(retried)
    assert second_id != first_id
    state = local_api.get(f"/api/agent-runs/{run_id}").json()
    assert state["status"] == "awaiting_review"
    history = state["result_json"]["workflow"]
    assert history["request_id"] == second_id and history["attempt"] == 2
    assert history["history"][0]["request_id"] == first_id and history["history"][0]["attempt"] == 1
    with trace_database() as db:
        rows = db.query(ModelRun).filter_by(agent_run_id=run_id, step_key="generate_draft", task_type="chat").all()
        assert sorted(row.workflow_attempt for row in rows) == [1, 2]
        step_id = rows[0].agent_step_id
        assert all(row.agent_step_id == step_id and row.input_preview is None and row.output_preview is None for row in rows)
    records = [record for record in events() if record["agent_run_id"] == run_id]
    assert {record["request_id"] for record in records if record["workflow_attempt"] == 1} == {first_id}
    assert {record["request_id"] for record in records if record["workflow_attempt"] == 2} == {second_id}
    failure_event = next(record for record in records if record["event"] == "workflow_step_failed")
    assert failure_event["agent_step_id"] == step_id and failure_event["step_key"] == "generate_draft"
    assert failure_event["error_type"] == ("IntegrityError" if failure == "transaction" else "UnexpectedError")
    finished = [record for record in records if record["event"] == "workflow_finished"]
    assert [record["status"] for record in finished] == ["failed", "awaiting_review"]
    assert all(record["elapsed_ms"] >= 0 for record in finished)
    assert_private_events(events)


def test_concurrent_real_tenant_backgrounds_do_not_share_request_or_run_identity(application, teams, monkeypatch, events):
    rendezvous = Barrier(2)
    async def concurrent_failure(run, req, topic, step, db):
        rendezvous.wait(timeout=10)
        LocalRuleBasedClient().chat(SECRET, SECRET)
        raise RuntimeError(SECRET)
    monkeypatch.setattr(workflow, "_step_generate_draft", concurrent_failure)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(team.client.post, "/api/agent-runs", json={
            "goal": SECRET, "provider": "local", "auto_score": False}) for team in teams]
        responses = [future.result(timeout=20) for future in futures]
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    ids = [request_id(response) for response in responses]
    assert ids[0] != ids[1]
    for team, response, correlation in zip(teams, responses, ids):
        run_id = response.json()["id"]
        records = [record for record in events() if record["agent_run_id"] == run_id
                   and record["request_id"] == correlation]
        assert {record["organization_id"] for record in records} == {team.org}
        assert any(record["event"] == "workflow_step_failed" for record in records)
        with business_db(team) as db:
            assert db.get(AgentRun, run_id).result_json["workflow"]["request_id"] == correlation
            assert db.get(AgentRun, run_id).status == "failed"
            rows = db.query(ModelRun).filter_by(agent_run_id=run_id).all()
            assert rows and all(row.workflow_attempt == 1 for row in rows)
    assert diagnostics.current_request_id.get() is None and current_tenant.get() is None
    assert_private_events(events)


def test_usage_ledger_hash_matches_trusted_request_and_workflow_attempt(application, teams, monkeypatch):
    from sqlalchemy import select
    from app.saas import store
    from app.saas.commerce_models import UsageEvent

    async def fail(*args, **kwargs):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(workflow, "_step_topic_ideas", fail)
    team = teams[0]
    response = team.client.post("/api/agent-runs", headers={"X-Request-ID": FORGED_ID},
        json={"goal": SECRET, "provider": "local", "auto_score": False})
    assert response.status_code == 201
    correlation = request_id(response)
    assert response.json()["result_json"]["workflow"]["request_id"] == correlation
    with store.session_factory() as db:
        entry = db.scalars(select(UsageEvent).where(UsageEvent.organization_id == team.org)).one()
        assert entry.request_hash == hashlib.sha256(correlation.encode()).hexdigest()
        assert entry.request_hash != hashlib.sha256(FORGED_ID.encode()).hexdigest()
    with business_db(team) as db:
        run = db.get(AgentRun, response.json()["id"])
        assert run.status == "failed"  # HTTP acceptance is not workflow success.
        assert run.result_json["workflow"]["request_id"] == correlation


def test_background_unpersistable_failure_restores_context_and_does_not_inherit_old_request(monkeypatch, events):
    parent = TenantContext("a" * 32, "b" * 32, "owner")
    child = TenantContext("c" * 32, "d" * 32, "owner")
    async def unavailable(run_id):
        assert diagnostics.current_request_id.get() is None
        assert current_tenant.get() == child
        raise OSError(SECRET)
    monkeypatch.setattr(agent_runs, "execute_agent_run", unavailable)
    tenant_token = current_tenant.set(parent)
    try:
        with diagnostics.request_diagnostic_context("a" * 32):
            agent_runs.execute_agent_run_background(19, child, None)
            assert diagnostics.current_request_id.get() == "a" * 32
            assert current_tenant.get() == parent
    finally:
        current_tenant.reset(tenant_token)
    record, = events()
    assert record["event"] == "workflow_background_failed" and record["request_id"] is None
    assert record["organization_id"] == child.organization_id and record["agent_run_id"] == 19
    assert record["workflow_attempt"] is None and record["error_type"] == "OSError"
    assert diagnostics.current_request_id.get() is None
    assert_private_events(events)


def test_exception_after_response_is_logged_without_replacing_response_or_leaking_trace(events):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"complete"})
        raise RuntimeError(SECRET)
    client = TestClient(diagnostics.RequestDiagnosticsMiddleware(app))
    try:
        response = client.get("/")
    finally:
        client.close()
    assert response.status_code == 200 and response.text == "complete"
    correlation = request_id(response)
    assert [record["event"] for record in events()] == ["http_response", "http_failed_after_response"]
    assert all(record["request_id"] == correlation for record in events())
    assert_private_events(events)


def test_diagnostic_sink_failure_does_not_fail_business_response(local_api, monkeypatch):
    monkeypatch.setattr(diagnostics.LOGGER, "log", lambda *a, **kw: (_ for _ in ()).throw(OSError(SECRET)))
    response = local_api.get("/health")
    assert response.status_code == 200 and request_id(response)


@pytest.mark.parametrize("entrypoint", ["string", "imported_app"])
def test_uvicorn_entrypoint_disables_raw_access_log_in_fresh_process(entrypoint):
    # Both start scripts invoke this import path. No socket or lifespan is started.
    root = Path(__file__).resolve().parents[1]
    script = """
import json, logging, sys, tempfile
sys.path.insert(0, 'scripts')
from evaluate_industrial_faq import configure_offline
with tempfile.TemporaryDirectory(prefix='diagnostics-import-') as folder:
    configure_offline(folder)
    sys.path.insert(0, 'backend')
    import uvicorn
    target = 'app.main:app'
    if sys.argv[1] == 'imported_app':
        from app.main import app
        target = app
    config = uvicorn.Config(target, lifespan='off')
    config.load()
    from app.main import app
    app.build_middleware_stack()
    print(json.dumps({'access_disabled': logging.getLogger('uvicorn.access').disabled,
                      'lifecycle_disabled': logging.getLogger('uvicorn.error').disabled}))
"""
    result = subprocess.run([sys.executable, "-c", script, entrypoint], cwd=root, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {"access_disabled": True, "lifecycle_disabled": False}

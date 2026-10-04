"""Scope proposals remain explicit, scoped and unconfirmed across real API boundaries.

Provider protocols use synthetic clients or the real SDK with MockTransport;
none connects to an external service or asserts real-vendor quality.
"""
import hashlib
import json
import re
from types import SimpleNamespace

import pytest

from app.agent_core import question_scope
from app.models.knowledge_base import KnowledgeBase
from app.models.workspace import Workspace
from test_required_facts import client, database, VOLTAGE, PRICE, query
from test_structured_facts import create_fact, index_fact
from test_saas_isolation import application, teams, business_db, invited_member

PATH = "/api/v04/rag/question-scope"
QUESTION = "合成星桥 XP-24 的额定电压 / 直流输入和售价是多少？"


def pairs(result):
    return [{key: item[key] for key in ("product_model", "parameter")} for item in result["candidates"]]


def test_proposal_does_not_read_catalog_or_erase_missing_requirements(client, monkeypatch):
    monkeypatch.setattr(question_scope.llm_router, "get_task_client", lambda *a, **kw: pytest.fail("rules must not invoke a model"))
    original = "  " + QUESTION + "\n"
    before = client.post(PATH, json={"query": original})
    assert before.status_code == 200, before.text
    first = before.json()
    assert first["query_hash"] == hashlib.sha256(original.encode()).hexdigest()
    assert pairs(first) == [VOLTAGE, PRICE]
    assert first["requires_confirmation"] is True and first["requirements_complete"] is False
    index_fact(client, create_fact(client))
    after = client.post(PATH, json={"query": original}).json()
    assert after == first  # Facts available in a catalog cannot narrow the question.
    # Scripted confirmation is API evidence, not evidence of a human's understanding.
    answered = query(client, pairs(first), query=QUESTION)
    assert answered["refused"] and answered["missing_facts"] == [PRICE]
    answered = query(client, [VOLTAGE], query=QUESTION)
    assert not answered["refused"] and answered["answerability"] == "required_facts_present"
    assert "24 V" in answered["answer"]


@pytest.mark.parametrize("changes", [
    {"query": ""}, {"query": " \t\n "}, {"query": "字" * 1001}, {"query": 123},
    {"method": "guess"}, {"method": "model"}, {"method": "model", "provider": "local"},
    {"provider": "deepseek"}, {"model": "hidden-online-default"},
    {"method": "model", "provider": "deepseek", "model": "m" * 101},
    {"workspace_id": 0}, {"knowledge_base_id": -1}, {"requirements_complete": True},
    {"required_facts": [VOLTAGE]},
])
def test_request_rejects_invalid_or_implicit_model_configuration(client, monkeypatch, changes):
    monkeypatch.setattr(question_scope, "propose_question_scope", lambda *a, **kw: pytest.fail("invalid input reached service"))
    assert client.post(PATH, json={"query": QUESTION, **changes}).status_code == 422


@pytest.mark.parametrize("body", [b'{"query":"\\ud800"}', b'{"query":"\\udfff"}',
    b'{"query":"SYNTHETIC_PRIVATE_MARKER","extra":true}', b'{"query":'])
def test_invalid_json_or_unicode_never_echoes_query_or_breaks_error_serialization(client, monkeypatch, body):
    monkeypatch.setattr(question_scope, "propose_question_scope", lambda *a, **kw: pytest.fail("invalid body reached service"))
    response = client.post(PATH, content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json() == {"detail": "问题范围或整理配置无效，请核对输入与模型设置"}


def test_scope_is_resolved_before_any_provider_request(client, database, monkeypatch):
    valid = client.post(PATH, json={"query": QUESTION}).json()
    with database() as db:
        other = Workspace(name="Synthetic other", slug="scope-other", is_default=False)
        db.add(other)
        db.flush()
        kb = KnowledgeBase(workspace_id=other.id, name="Synthetic other KB", status="active")
        db.add(kb)
        db.commit()
        other_id = kb.id
    monkeypatch.setattr(question_scope, "propose_question_scope", lambda *a, **kw: pytest.fail("invalid scope reached provider"))
    for scope in ({"workspace_id": 9999}, {"knowledge_base_id": 9999},
                  {"workspace_id": valid["workspace_id"], "knowledge_base_id": other_id}):
        response = client.post(PATH, json={"query": QUESTION, "method": "model", "provider": "deepseek", **scope})
        assert response.status_code == 422
        assert response.json() == {"detail": "问题范围或整理配置无效，请核对输入与模型设置"}


@pytest.mark.parametrize("code,status", [("invalid_scope_proposal", 502), ("scope_provider_failed", 503)])
def test_expected_model_errors_have_safe_machine_codes_and_no_fallback(client, monkeypatch, code, status):
    def fail(*args, **kwargs):
        raise question_scope.QuestionScopeError(code)
    monkeypatch.setattr(question_scope, "propose_question_scope", fail)
    response = client.post(PATH, json={"query": QUESTION, "method": "model", "provider": "deepseek"})
    assert response.status_code == status
    assert response.json() == {"detail": str(question_scope.QuestionScopeError(code)), "code": code}


@pytest.fixture
def model_protocol(monkeypatch):
    from app.llm.router import ModelRouter, router
    state = SimpleNamespace(calls=[], payload=None, failure=None)
    class CachedClient:
        model = "synthetic-scope-model"
        def chat_json(self, system, user, **kwargs):
            state.calls.append((system, user, kwargs))
            if state.failure:
                raise state.failure
            return state.payload
    monkeypatch.setattr(ModelRouter, "PROVIDERS", {**ModelRouter.PROVIDERS, "deepseek": {
        "api_key": "synthetic-not-a-real-key", "model": CachedClient.model, "base_url": "https://provider.invalid"}})
    router._clients["deepseek:" + CachedClient.model] = CachedClient()
    proposal = question_scope.propose_question_scope(QUESTION)
    state.payload = {key: proposal[key] for key in ("candidates", "issues")}
    return state


def usage(identity):
    response = identity.client.get("/api/saas/billing")
    assert response.status_code == 200, response.text
    return response.json()["usage"]["ai_requests"]


def test_saas_rules_are_available_to_viewer_without_model_calls_logs_or_quota(application, teams, monkeypatch):
    from app.models.model_run import ModelRun
    a, _ = teams
    viewer = invited_member(application, a, "viewer")
    monkeypatch.setattr(question_scope.llm_router, "get_task_client", lambda *a, **kw: pytest.fail("rules invoked provider"))
    response = viewer.client.post(PATH, json={"query": QUESTION})
    assert response.status_code == 200, response.text
    assert pairs(response.json()) == [VOLTAGE, PRICE]
    assert usage(a)["attempts"] == 0
    with business_db(a) as db:
        assert db.query(ModelRun).count() == 0


def test_auth_csrf_and_other_organization_stop_before_proposals(application, teams, monkeypatch):
    a, b = teams
    monkeypatch.setattr(question_scope, "propose_question_scope", lambda *a, **kw: pytest.fail("unauthorized request reached service"))
    body = {"query": QUESTION, "method": "model", "provider": "deepseek"}
    assert application.anonymous.post(PATH, json=body).status_code in {401, 403}
    assert a.client.post(PATH, json=body, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert a.client.post(PATH, json=body, headers={"X-Organization-ID": b.org}).status_code == 403
    assert usage(a)["attempts"] == usage(b)["attempts"] == 0


def test_enabled_model_charged_once_disabled_cached_model_never_runs(teams, model_protocol):
    a, b = teams
    body = {"query": QUESTION, "method": "model", "provider": "deepseek"}
    assert a.client.patch("/api/saas/connections/deepseek", json={"enabled": True}).status_code == 200
    response = a.client.post(PATH, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["model"] == "synthetic-scope-model"
    assert len(model_protocol.calls) == 1
    assert usage(a)["used"] == 1 and usage(b)["attempts"] == 0
    assert b.client.post(PATH, json=body).status_code == 422
    assert a.client.patch("/api/saas/connections/deepseek", json={"enabled": False}).status_code == 200
    assert a.client.post(PATH, json=body).status_code == 422
    assert len(model_protocol.calls) == 1
    assert usage(a)["used"] == 1 and usage(a)["reserved"] == 0


@pytest.mark.parametrize("failure,status", [(None, 502), (RuntimeError("synthetic-private-provider-detail"), 503)])
def test_rejected_model_proposal_refunds_internal_quota_not_upstream_billing(teams, model_protocol, failure, status):
    a, _ = teams
    assert a.client.patch("/api/saas/connections/deepseek", json={"enabled": True}).status_code == 200
    model_protocol.payload = {"answer": "This is not a proposal"}
    model_protocol.failure = failure
    response = a.client.post(PATH, json={"query": QUESTION, "method": "model", "provider": "deepseek"})
    assert response.status_code == status, response.text
    assert "synthetic-private-provider-detail" not in response.text
    assert len(model_protocol.calls) == 1  # Upstream work may already have been billed.
    record = usage(a)
    assert record["used"] == record["reserved"] == 0 and record["refunded"] == 1


@pytest.fixture
def sdk_protocol(application, monkeypatch):
    """Keep routing, SDK, trace persistence and tenant stores real; stub HTTP only."""
    import httpx
    from openai import OpenAI
    from app.core.config import settings
    from app.llm.router import ModelRouter

    proposal = question_scope.propose_question_scope(QUESTION)
    state = SimpleNamespace(calls=[], payload={key: proposal[key] for key in ("candidates", "issues")})
    model_name = "synthetic-scope-sdk-protocol"
    monkeypatch.setattr(settings, "MODEL_PRICING_JSON", "[]")
    monkeypatch.setattr(ModelRouter, "PROVIDERS", {**ModelRouter.PROVIDERS, "deepseek": {
        "api_key": "sk-SYNTHETIC_SCOPE_PRIVATE", "model": model_name,
        "base_url": "https://synthetic-scope.invalid/v1"}})

    def respond(request):
        state.calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "synthetic-scope", "object": "chat.completion", "created": 1, "model": model_name,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(state.payload, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18},
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        monkeypatch.setattr("app.llm.openai_compatible.OpenAI",
                            lambda **kwargs: OpenAI(http_client=transport, **kwargs))
        yield state


@pytest.mark.parametrize("invalid_schema,status", [(False, 200), (True, 502)])
def test_saas_real_sdk_logs_current_tenant_even_when_proposal_schema_is_rejected(teams, sdk_protocol, invalid_schema, status):
    from app.models.model_run import ModelRun
    from app.llm.tracing import current_model_trace, serialize_model_run

    a, b = teams
    assert a.client.patch("/api/saas/connections/deepseek", json={"enabled": True}).status_code == 200
    if invalid_schema:
        sdk_protocol.payload = {"answer": "SYNTHETIC_PRIVATE_PROVIDER_OUTPUT"}
    response = a.client.post(PATH, json={"query": QUESTION, "method": "model", "provider": "deepseek"})
    assert response.status_code == status, response.text
    assert len(sdk_protocol.calls) == 1  # Schema rejection never adds a semantic repair call.
    if invalid_schema:
        assert response.json()["code"] == "invalid_scope_proposal"
    else:
        assert pairs(response.json()) == [VOLTAGE, PRICE]
    with business_db(a) as db:
        row = db.query(ModelRun).one()
        assert row.provider == "deepseek" and row.model_name == "synthetic-scope-sdk-protocol"
        assert row.step_key == "question_scope"
        assert row.agent_run_id is row.agent_step_id is row.workflow_attempt is None
        assert re.fullmatch(r"[0-9a-f]{32}", row.invocation_id)
        assert row.request_index == 1 and row.call_kind == "initial"
        # This describes the upstream SDK response, not proposal schema acceptance.
        assert row.success is True and row.error_type is None
        assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (7, 11, 18)
        assert row.cost_status == "unpriced" and row.estimated_cost is None
        assert row.input_preview is row.output_preview is row.error_message is None
        messages = sdk_protocol.calls[0]["messages"]
        assert row.prompt_version == hashlib.sha256(messages[0]["content"].encode()).hexdigest()
        expected_hash = hashlib.sha256(json.dumps(
            [[message["role"], message["content"]] for message in messages],
            ensure_ascii=False, separators=(",", ":"),
        ).encode()).hexdigest()
        assert row.prompt_hash == expected_hash
        saved = serialize_model_run(row)
    with business_db(b) as db:
        assert db.query(ModelRun).count() == 0
    listed = a.client.get("/api/models/runs")
    assert listed.status_code == 200 and listed.json()["runs"] == [saved]
    assert b.client.get("/api/models/runs").json()["runs"] == []
    for private_text in (QUESTION, "sk-SYNTHETIC_SCOPE_PRIVATE", "SYNTHETIC_PRIVATE_PROVIDER_OUTPUT"):
        assert private_text not in json.dumps(saved, ensure_ascii=False)
        assert private_text not in listed.text
    assert current_model_trace.get() is None
    counter = usage(a)
    assert counter["used"] == (0 if invalid_schema else 1)
    assert counter["refunded"] == (1 if invalid_schema else 0)
    assert counter["reserved"] == 0 and usage(b)["attempts"] == 0


def test_saas_scope_quota_exhaustion_stops_before_service_and_provider(teams, sdk_protocol, monkeypatch):
    from app.models.model_run import ModelRun
    from app.saas.commerce import provision_plan

    a, b = teams
    provision_plan(a.org, code="team", limits={"ai_requests": 0, "documents": 10, "members": 3})
    assert a.client.patch("/api/saas/connections/deepseek", json={"enabled": True}).status_code == 200
    monkeypatch.setattr(question_scope, "propose_question_scope", lambda *a, **kw: pytest.fail("exhausted quota reached service"))
    response = a.client.post(PATH, json={"query": QUESTION, "method": "model", "provider": "deepseek"})
    assert response.status_code == 429, response.text
    assert sdk_protocol.calls == []
    for identity in (a, b):
        with business_db(identity) as db:
            assert db.query(ModelRun).count() == 0
        assert usage(identity)["attempts"] == 0

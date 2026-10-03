"""Explicit requested facts gate retrieval before any model or draft generation."""
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_core import rag_service
from app.agent_core.boundaries import workspace_context
from app.api.routes import agent_runs, evidence, v04
from app.api.routes.knowledge import DocumentUpload, upload_document
from app.db.session import get_db
from app.llm.local import LocalRuleBasedClient
from app.models.draft import Draft
from app.models.topic import Topic
from app.services import content_growth_agent as workflow

from test_evidence_notes import database
from test_structured_facts import create_fact, index_fact


VOLTAGE = {"product_model": "合成星桥 XP-24", "parameter": "额定电压 / 直流输入"}
PRICE = {"product_model": "合成星桥 XP-24", "parameter": "售价"}


@pytest.fixture
def client(database, monkeypatch):
    app = FastAPI()
    for router in (evidence.router, v04.router, agent_runs.router):
        app.include_router(router, prefix="/api")

    def sessions():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = sessions
    monkeypatch.setattr(workflow, "SessionLocal", database)
    monkeypatch.setattr(LocalRuleBasedClient, "_log_run", lambda *args, **kwargs: None)
    with TestClient(app) as result:
        yield result


def query(client, required_facts=None, **kwargs):
    body = {"query": "合成星桥 XP-24 额定电压", **kwargs}
    if required_facts is not None:
        body["required_facts"] = required_facts
    result = client.post("/api/v04/rag/answer", json=body)
    assert result.status_code == 200, result.text
    return result.json()


def test_without_explicit_requirements_answerability_is_never_assumed(client):
    empty = query(client)
    assert empty["refused"] and empty["answerability"] == "not_assessed"
    index_fact(client, create_fact(client))
    related = query(client, query="合成星桥 XP-24 售价")
    assert not related["refused"] and related["answerability"] == "not_assessed"
    assert related["required_facts"] == related["missing_facts"] == []


def test_required_facts_success_returns_complete_located_structured_excerpt(client):
    index_fact(client, create_fact(client))
    result = query(client, [{"product_model": "  合成星桥 XP-24  ", "parameter": "  额定电压 / 直流输入  "}])
    assert not result["refused"]
    assert result["required_facts"] == [VOLTAGE]
    assert result["answerability"] == "required_facts_present"
    assert result["missing_facts"] == []
    assert result["matched_facts"][0]["value"] == "24 V"
    assert result["matched_facts"][0]["excerpt"] in result["answer"]
    assert "synthetic-v1" in result["answer"] and "24 V" in result["answer"]


@pytest.mark.parametrize("requirements,missing", [
    ([PRICE], [PRICE]), ([VOLTAGE, PRICE], [PRICE]),
    ([{"product_model": "另一品牌 XP-24", "parameter": VOLTAGE["parameter"]}],
     [{"product_model": "另一品牌 XP-24", "parameter": VOLTAGE["parameter"]}]),
])
def test_related_product_hit_does_not_satisfy_missing_parameter_or_brand(client, monkeypatch, requirements, missing):
    index_fact(client, create_fact(client))
    monkeypatch.setattr(rag_service.llm_router, "get_task_client", lambda *a, **kw: pytest.fail("missing facts must stop before model"))
    result = query(client, requirements, provider="test-cloud")
    assert result["refused"] and result["refusal_reason"] == "missing_structured_facts"
    assert result["answerability"] == "missing_required_facts" and result["missing_facts"] == missing
    assert result["eligible_evidence_count"] > 0
    assert result["citations"] == []


@pytest.mark.parametrize("bad_facts", [
    [{"product_model": " ", "parameter": "售价"}],
    [{"product_model": "合成星桥", "parameter": " "}],
    [{"product_model": "x" * 201, "parameter": "售价"}],
    [{"product_model": "合成星桥", "parameter": "x" * 201}],
    [{"product_model": "合成星桥", "parameter": "售价", "invented": "field"}],
    [PRICE] * 11,
])
@pytest.mark.parametrize("path,body", [
    ("/api/v04/rag/answer", {"query": "合成问题"}),
    ("/api/agent-runs", {"goal": "合成任务", "use_rag": True}),
])
def test_required_fact_schema_is_bounded_and_rejects_blank_or_unknown_fields(client, bad_facts, path, body):
    result = client.post(path, json={**body, "required_facts": bad_facts})
    assert result.status_code == 422, result.text


def test_agent_cannot_disable_retrieval_to_bypass_fact_requirements(client):
    result = client.post("/api/agent-runs", json={"goal": "合成任务", "use_rag": False, "required_facts": [PRICE]})
    assert result.status_code == 422 and "必须启用知识库检索" in result.text


def test_revoked_structured_evidence_is_missing_even_if_old_metadata_remains(client):
    note = create_fact(client)
    index_fact(client, note)
    client.post(f"/api/evidence/notes/{note['id']}/review", json={"decision": "revoke"})
    result = query(client, [VOLTAGE])
    assert result["refused"] and result["refusal_reason"] == "missing_structured_facts"
    assert result["missing_facts"] == [VOLTAGE]


def test_untracked_upload_cannot_satisfy_explicit_reviewed_fact(client, database):
    with database() as db:
        upload_document(DocumentUpload(title="合成未核验电压说明", content=
            "合成星桥 XP-24 的额定电压为 24 V，适用于直流输入。这只是普通上传，尚未记录产品参数、资料版本和逐条核验决定。"), db)
    result = query(client, [VOLTAGE])
    assert result["eligible_evidence_count"] > 0
    assert result["refused"] and result["missing_facts"] == [VOLTAGE]


@pytest.mark.parametrize("mutation", ["unseen_excerpt", "bad_url", "missing_value", "not_reviewed"])
def test_matching_requires_selected_passage_complete_value_and_valid_provenance(client, database, mutation):
    index_fact(client, create_fact(client))
    with database() as db:
        hits = rag_service.select_evidence(rag_service.search_knowledge("合成星桥 XP-24 额定电压", db, workspace_context(db)))
    hits = deepcopy(hits)
    for hit in hits:
        metadata = hit.metadata["evidence"]
        if mutation == "unseen_excerpt":
            hit.content = "同一文档中的另一段，只包含相关产品名，不含电压出处。"
        elif mutation == "bad_url":
            metadata["citations"][0]["source_url"] = "http://127.0.0.1/private"
        elif mutation == "missing_value":
            metadata["citations"][0]["value"] = ""
        else:
            metadata["status"] = "pending"
    result = rag_service.assess_required_facts(hits, [VOLTAGE])
    assert result["missing_facts"] == [VOLTAGE]


def test_agent_stops_before_generation_persists_requirements_and_retains_them_on_retry(client, database, monkeypatch):
    index_fact(client, create_fact(client))
    monkeypatch.setattr(workflow, "generate_custom_topic_ideas", lambda *a, **kw: pytest.fail("must stop before topic model"))
    created = client.post("/api/agent-runs", json={
        "goal": "合成星桥 XP-24 售价 FAQ", "use_rag": True, "provider": "local", "required_facts": [PRICE],
    })
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    result = client.get(f"/api/agent-runs/{run_id}").json()
    assert result["status"] == "failed" and result["draft_id"] is None
    assert result["result_json"]["_request"]["required_facts"] == [PRICE]
    assert result["result_json"]["rag_context"]["missing_facts"] == [PRICE]
    assert result["result_json"]["failure"]["step"] == "retrieve_context"
    assert "售价" in result["error_message"]
    retried = client.post(f"/api/agent-runs/{run_id}/retry")
    assert retried.status_code == 200, retried.text
    after = client.get(f"/api/agent-runs/{run_id}").json()
    assert after["status"] == "failed" and after["result_json"]["rag_context"]["missing_facts"] == [PRICE]
    with database() as db:
        assert db.query(Topic).count() == 0 and db.query(Draft).count() == 0


def test_agent_with_supported_requirement_proceeds_to_human_review(client):
    index_fact(client, create_fact(client))
    created = client.post("/api/agent-runs", json={
        "goal": "合成星桥 XP-24 额定电压 FAQ", "use_rag": True, "provider": "local", "required_facts": [VOLTAGE],
    })
    assert created.status_code == 201, created.text
    result = client.get(f"/api/agent-runs/{created.json()['id']}").json()
    assert result["status"] == "awaiting_review", result
    assert result["result_json"]["rag_context"]["answerability"] == "required_facts_present"


def test_retry_rechecks_live_facts_after_a_later_step_failed(client, monkeypatch):
    note = create_fact(client)
    index_fact(client, note)
    calls = []

    async def fail_topic(*args, **kwargs):
        calls.append("topic")
        raise RuntimeError("controlled synthetic failure after successful retrieval")

    monkeypatch.setattr(workflow, "generate_custom_topic_ideas", fail_topic)
    created = client.post("/api/agent-runs", json={
        "goal": "合成星桥 XP-24 额定电压 FAQ", "use_rag": True, "provider": "local", "required_facts": [VOLTAGE],
    })
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    first = client.get(f"/api/agent-runs/{run_id}").json()
    assert first["result_json"]["failure"]["step"] == "topic_ideas"
    client.post(f"/api/evidence/notes/{note['id']}/review", json={"decision": "revoke"})
    retried = client.post(f"/api/agent-runs/{run_id}/retry")
    assert retried.status_code == 200, retried.text
    after = client.get(f"/api/agent-runs/{run_id}").json()
    assert after["status"] == "failed"
    assert after["result_json"]["failure"]["step"] == "retrieve_context"
    assert after["result_json"]["rag_context"]["missing_facts"] == [VOLTAGE]
    assert calls == ["topic"]

"""Real tool/direct API parity with synthetic evidence in temporary lexical stores.

No live model is used: model acquisition and network connections fail the tests.
Workspace/KB checks establish local data scoping, not user authentication.
"""
import os
from pathlib import Path
import socket
import sys

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_core import rag_service, tools
from app.api.routes import evidence, v04
from app.db.session import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.workspace import Workspace
from app.schemas.evidence import RequiredFact

from test_evidence_notes import database
from test_structured_facts import create_fact, fact_payload, index_fact


QUERY = "合成星桥 XP-24 的额定电压和售价是多少？"
VOLTAGE = {"product_model": "合成星桥 XP-24", "parameter": "额定电压 / 直流输入"}
PRICE = {"product_model": "合成星桥 XP-24", "parameter": "售价"}
RESPONSE_TIME = {"product_model": "合成星桥 XP-24", "parameter": "售后响应时间"}
WRONG_MODEL = {**VOLTAGE, "product_model": "合成星桥 XP-48"}
TOOL_PATH = "/api/v04/tools/execute"
DIRECT_PATH = "/api/v04/rag/answer"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("SAAS_MODE", "false")

    def forbidden(*args, **kwargs):
        pytest.fail("These lexical tool tests must not acquire a model or access the network")

    monkeypatch.setattr(rag_service.llm_router, "get_task_client", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


@pytest.fixture
def client(database):
    app = FastAPI()
    app.include_router(evidence.router, prefix="/api")
    app.include_router(v04.router, prefix="/api")

    def sessions():
        with database() as db:
            yield db

    app.dependency_overrides[get_db] = sessions
    with TestClient(app) as result:
        yield result


def call_pair(client, arguments, **scope):
    tool = client.post(TOOL_PATH, json={"tool_name": "rag.answer", "arguments": arguments, **scope})
    direct = client.post(DIRECT_PATH, json={**arguments, **scope})
    assert tool.status_code == direct.status_code == 200, (tool.text, direct.text)
    wrapped = tool.json()
    assert wrapped["output"] == direct.json()
    assert wrapped["tool_name"] == "rag.answer" and wrapped["writes"] is False
    for field, value in scope.items():
        assert wrapped[field] == value
    return wrapped["output"]


def test_allowlist_publishes_shared_fact_schema_and_rejects_unknown_arguments(client):
    response = client.get("/api/v04/tools")
    assert response.status_code == 200
    spec = next(item for item in response.json()["items"] if item["name"] == "rag.answer")
    schema = spec["input_schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["required_facts"]["maxItems"] == 10
    assert schema["properties"]["required_facts"]["items"] == {"$ref": "#/$defs/RequiredFact"}
    assert schema["$defs"]["RequiredFact"] == RequiredFact.model_json_schema()
    assert "required_facts" in spec["description"]
    assert any("not_assessed" in rule for rule in spec["boundary_rules"])


def test_supported_fact_is_passed_to_real_service_with_identical_direct_answer(client):
    indexed = index_fact(client, create_fact(client))
    required = [{"product_model": "  合成星桥 XP-24  ", "parameter": "  额定电压 / 直流输入  "}]
    result = call_pair(client, {"query": QUERY, "required_facts": required, "retrieval_mode": "lexical"})
    assert result["required_facts"] == [VOLTAGE]
    assert result["answerability"] == "required_facts_present"
    assert result["answer_validation"]["status"] == "matched"
    assert not result["refused"] and result["missing_facts"] == []
    assert result["matched_facts"][0]["value"] == "24 V"
    assert result["matched_facts"][0]["excerpt"] in result["answer"]
    assert {citation["document_id"] for citation in result["citations"]} == {indexed["document_id"]}


@pytest.mark.parametrize("requirements,missing", [
    ([PRICE], [PRICE]),
    ([RESPONSE_TIME], [RESPONSE_TIME]),
    ([WRONG_MODEL], [WRONG_MODEL]),
    ([VOLTAGE, PRICE], [PRICE]),
])
def test_missing_parameter_wrong_model_and_partial_coverage_stop_before_model(client, requirements, missing):
    index_fact(client, create_fact(client))
    result = call_pair(client, {"query": QUERY, "required_facts": requirements, "provider": "synthetic-cloud"})
    assert result["eligible_evidence_count"] > 0  # Related evidence exists but is insufficient.
    assert result["refused"] and result["refusal_reason"] == "missing_structured_facts"
    assert result["missing_facts"] == missing and result["citations"] == []
    assert result["answerability"] == "missing_required_facts"
    assert result["answer_validation"]["status"] == "not_assessed"


def test_empty_library_reports_requested_missing_facts_before_model(client):
    result = call_pair(client, {"query": QUERY, "required_facts": [VOLTAGE], "provider": "synthetic-cloud"})
    assert result["eligible_evidence_count"] == 0
    assert result["refused"] and result["refusal_reason"] == "missing_structured_facts"
    assert result["missing_facts"] == [VOLTAGE]


@pytest.mark.parametrize("requirements", [None, []])
def test_omitted_or_empty_requirements_remain_explicitly_unassessed(client, requirements):
    index_fact(client, create_fact(client))
    arguments = {"query": QUERY}
    if requirements is not None:
        arguments["required_facts"] = requirements
    result = call_pair(client, arguments)
    assert not result["refused"]  # Compatibility: this is still free-form related-evidence mode.
    assert result["answerability"] == result["answer_validation"]["status"] == "not_assessed"
    assert result["required_facts"] == result["missing_facts"] == []


@pytest.mark.parametrize("bad_facts", [
    None,
    "售价",
    [{}],
    [{"product_model": " ", "parameter": "售价"}],
    [{"product_model": "合成星桥 XP-24", "parameter": " "}],
    [{"product_model": "x" * 201, "parameter": "售价"}],
    [{"product_model": "合成星桥 XP-24", "parameter": "x" * 201}],
    [{**PRICE, "value": "99 测试币"}],
    [PRICE] * 11,
])
def test_bad_facts_are_rejected_like_direct_api_before_retrieval(client, monkeypatch, bad_facts):
    monkeypatch.setattr(rag_service, "search_knowledge", lambda *a, **k: pytest.fail("invalid arguments must not retrieve"))
    arguments = {"query": QUERY, "required_facts": bad_facts}
    tool = client.post(TOOL_PATH, json={"tool_name": "rag.answer", "arguments": arguments})
    direct = client.post(DIRECT_PATH, json=arguments)
    assert tool.status_code == direct.status_code == 422
    assert "required_facts" in tool.text and "工具参数不合法" in tool.text


@pytest.mark.parametrize("extra", [
    {"required_fact": [PRICE]},
    {"workspace_id": 2},
    {"knowledge_base_id": 2},
    {"allow_missing_facts": True},
])
def test_unknown_tool_arguments_cannot_silently_drop_requirements_or_override_scope(client, monkeypatch, extra):
    monkeypatch.setattr(tools, "answer_question", lambda *a, **k: pytest.fail("invalid arguments must not invoke service"))
    response = client.post(TOOL_PATH, json={"tool_name": "rag.answer", "arguments": {
        "query": QUERY, "required_facts": [PRICE], **extra,
    }})
    assert response.status_code == 422
    assert next(iter(extra)) in response.text and "Extra inputs are not permitted" in response.text


@pytest.mark.parametrize("changes", [
    {"query": ""}, {"query": "x" * 1001}, {"top_k": 0}, {"top_k": 13},
    {"provider": "x" * 81}, {"model": "x" * 161}, {"retrieval_mode": "unbounded"},
])
def test_tool_argument_limits_still_reject_before_execution(client, monkeypatch, changes):
    monkeypatch.setattr(tools, "answer_question", lambda *a, **k: pytest.fail("invalid arguments must not invoke service"))
    response = client.post(TOOL_PATH, json={"tool_name": "rag.answer", "arguments": {
        "query": QUERY, "required_facts": [VOLTAGE], **changes,
    }})
    assert response.status_code == 422 and next(iter(changes)) in response.text


def test_fact_limits_accept_ten_items_and_two_hundred_character_fields(client):
    requirements = [{"product_model": "p" * 200, "parameter": f"{number:02}" + "x" * 198} for number in range(10)]
    result = call_pair(client, {"query": QUERY, "required_facts": requirements, "provider": "synthetic-cloud"})
    assert result["missing_facts"] == requirements


@pytest.mark.parametrize("boundary", ["knowledge_base", "workspace"])
def test_facts_in_another_scope_cannot_complete_the_current_request(client, database, boundary):
    current = create_fact(client)
    current_index = index_fact(client, current)
    current_scope = {key: current[key] for key in ("workspace_id", "knowledge_base_id")}
    with database() as db:
        workspace_id = current["workspace_id"]
        if boundary == "workspace":
            workspace = Workspace(name="合成另一工作区", slug="synthetic-other", is_default=False)
            db.add(workspace)
            db.flush()
            workspace_id = workspace.id
        kb = KnowledgeBase(workspace_id=workspace_id, name="合成另一资料库", status="active")
        db.add(kb)
        db.commit()
        other_scope = {"workspace_id": workspace_id, "knowledge_base_id": kb.id}

    excerpt = "合成星桥 XP-24 的售价为 99 测试币，仅是自编测试数据，不代表实际产品定价。"
    citation = {**fact_payload()["citations"][0], "parameter": "售价", "value": "99 测试币",
                "claim": excerpt, "excerpt": excerpt}
    other = create_fact(client, content="【合成隔离资料】" + excerpt, citations=[citation], **other_scope)
    other_index = index_fact(client, other)

    missing = call_pair(client, {"query": QUERY, "required_facts": [VOLTAGE, PRICE], "provider": "synthetic-cloud"}, **current_scope)
    assert missing["refused"] and missing["missing_facts"] == [PRICE]
    assert {item["document_id"] for item in missing["matched_facts"]} == {current_index["document_id"]}
    assert {item["document_id"] for item in missing["retrieval_candidates"]} == {current_index["document_id"]}
    allowed = call_pair(client, {"query": QUERY, "required_facts": [PRICE]}, **other_scope)
    assert not allowed["refused"]
    assert {item["document_id"] for item in allowed["citations"]} == {other_index["document_id"]}

    if boundary == "workspace":
        mismatch = {"workspace_id": current["workspace_id"], "knowledge_base_id": other_scope["knowledge_base_id"]}
        body = {"query": QUERY, "required_facts": [PRICE], "provider": "synthetic-cloud"}
        tool = client.post(TOOL_PATH, json={"tool_name": "rag.answer", "arguments": body, **mismatch})
        direct = client.post(DIRECT_PATH, json={**body, **mismatch})
        assert tool.status_code == direct.status_code == 422
        assert "不属于当前 workspace" in tool.text


def test_existing_search_tool_remains_usable(client):
    indexed = index_fact(client, create_fact(client))
    response = client.post(TOOL_PATH, json={"tool_name": "rag.search", "arguments": {"query": QUERY}})
    assert response.status_code == 200, response.text
    assert {item["document_id"] for item in response.json()["output"]["items"]} == {indexed["document_id"]}

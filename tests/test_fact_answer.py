"""A valid citation cannot justify a wrong or omitted industrial parameter."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.agent_core import rag_service
from app.agent_core.fact_answer import FactAnswerError, validate_fact_answer
from app.models.evidence_note import EvidenceNote

from test_required_facts import VOLTAGE, client, database, query
from test_structured_facts import create_fact, index_fact


def fact_contract(client):
    note = create_fact(client)
    index_fact(client, note)
    local = query(client, [VOLTAGE])
    fact = local["matched_facts"][0]
    claim = {key: fact[key] for key in ("product_model", "parameter", "value", "chunk_id")}
    return note, fact, {"claims": [claim]}


def model(monkeypatch, callback):
    def chat_json(system_prompt, user_prompt, temperature):
        assert temperature == 0
        assert "不可信" in system_prompt and "JSON" in system_prompt
        prompt = json.loads(user_prompt)
        assert prompt["supplied_facts"]
        return callback(prompt)
    monkeypatch.setattr(rag_service.llm_router, "get_task_client", lambda *a, **kw: SimpleNamespace(chat_json=chat_json))


def test_valid_model_claims_are_rendered_from_reviewed_values_and_locators(client, monkeypatch):
    _, fact, payload = fact_contract(client)
    # Normalized labels are accepted, but the authoritative spelling is rendered.
    payload["claims"][0]["value"] = " 24  V "
    model(monkeypatch, lambda prompt: payload)
    result = query(client, [VOLTAGE], provider="test-cloud")
    assert not result["refused"]
    assert result["answer_validation"]["status"] == "matched"
    assert "模型参数声明已与所列资料核对" in result["answer"]
    assert fact["value"] in result["answer"] and fact["locator"] in result["answer"]
    assert fact["version_label"] in result["answer"]
    assert f"[chunk:{fact['chunk_id']}]" in result["answer"]
    assert "未调用生成模型" not in result["answer"]


@pytest.mark.parametrize("mutation,reason", [
    ("wrong_value", "value_mismatch"), ("wrong_model", "unexpected_claim"),
    ("wrong_parameter", "unexpected_claim"), ("wrong_chunk", "citation_mismatch"),
    ("extra_price", "unexpected_claim"), ("duplicate", "duplicate_claim"),
    ("empty", "invalid_structure"), ("free_prose", "invalid_structure"),
    ("claim_prose", "invalid_structure"), ("bool_chunk", "invalid_structure"),
    ("string_chunk", "invalid_structure"), ("non_object", "invalid_structure"),
])
def test_valid_retrieval_does_not_accept_incorrect_model_assertions(client, monkeypatch, mutation, reason):
    _, _, payload = fact_contract(client)
    claim = payload["claims"][0]
    if mutation == "wrong_value":
        claim["value"] = "480 V"
    elif mutation == "wrong_model":
        claim["product_model"] = "另一型号 XP-48"
    elif mutation == "wrong_parameter":
        claim["parameter"] = "售价"
    elif mutation == "wrong_chunk":
        claim["chunk_id"] += 999
    elif mutation == "extra_price":
        payload["claims"].append({**claim, "parameter": "售价", "value": "199元"})
    elif mutation == "duplicate":
        payload["claims"].append(deepcopy(claim))
    elif mutation == "empty":
        payload["claims"] = []
    elif mutation == "free_prose":
        payload["answer"] = "未经校验的承诺及敏感内容"
    elif mutation == "claim_prose":
        claim["explanation"] = "未经校验的承诺及敏感内容"
    elif mutation == "bool_chunk":
        claim["chunk_id"] = True
    elif mutation == "string_chunk":
        claim["chunk_id"] = str(claim["chunk_id"])
    else:
        payload = "未经校验的承诺及敏感内容"
    model(monkeypatch, lambda prompt: payload)
    result = query(client, [VOLTAGE], provider="test-cloud")
    assert result["refused"] and result["refusal_reason"] == "invalid_fact_answer"
    assert result["answer_validation"]["status"] == "failed"
    assert result["answer_validation"]["reason"] == reason
    assert result["citations"] == []
    assert "480 V" not in result["answer"] and "未经校验的承诺及敏感内容" not in result["answer"]


def test_invalid_json_failure_is_sanitized_and_does_not_return_provider_output(client, monkeypatch):
    fact_contract(client)
    def invalid(prompt):
        raise ValueError("synthetic provider payload must remain private")
    model(monkeypatch, invalid)
    result = query(client, [VOLTAGE], provider="test-cloud")
    assert result["refused"] and result["refusal_reason"] == "invalid_fact_answer"
    assert "provider payload" not in json.dumps(result)


@pytest.mark.parametrize("mutation", ["revoke", "version", "value"])
def test_model_answer_cannot_use_fact_assertions_changed_during_generation(client, database, monkeypatch, mutation):
    note, _, payload = fact_contract(client)
    def change_during_call(prompt):
        if mutation == "revoke":
            response = client.post(f"/api/evidence/notes/{note['id']}/review", json={"decision": "revoke"})
            assert response.status_code == 200
        else:
            # Simulate committed metadata changes without content hash changes.
            with database() as db:
                stored = db.get(EvidenceNote, note["id"])
                if mutation == "version":
                    stored.version_label = "synthetic-v2"
                else:
                    citations = deepcopy(stored.citations)
                    citations[0]["value"] = "48 V"
                    stored.citations = citations
                db.commit()
        return payload
    model(monkeypatch, change_during_call)
    result = query(client, [VOLTAGE], provider="test-cloud")
    assert result["refused"] and result["refusal_reason"] == "evidence_changed"
    assert result["answer_validation"]["status"] == "not_assessed"
    assert result["matched_facts"] == [] and result["citations"] == []


def test_compound_contract_requires_all_facts_and_preserves_unit_case():
    voltage = {**VOLTAGE, "value": "24 V", "chunk_id": 1}
    current = {"product_model": VOLTAGE["product_model"], "parameter": "额定电流", "value": "5 mA", "chunk_id": 2}
    with pytest.raises(FactAnswerError, match="模型参数声明") as missing:
        validate_fact_answer({"claims": [voltage]}, [voltage, current])
    assert missing.value.reason == "missing_claim"
    with pytest.raises(FactAnswerError) as wrong_units:
        validate_fact_answer({"claims": [voltage, {**current, "value": "5 MA"}]}, [voltage, current])
    assert wrong_units.value.reason == "value_mismatch"
    validate_fact_answer({"claims": [current, voltage]}, [voltage, current])


def test_non_contract_answer_remains_explicitly_unassessed(client, monkeypatch):
    _, fact, _ = fact_contract(client)
    monkeypatch.setattr(rag_service.llm_router, "get_task_client", lambda *a, **kw: SimpleNamespace(
        chat=lambda *a, **kw: f"相关原文 [chunk:{fact['chunk_id']}]")
    )
    result = query(client, provider="test-cloud")
    assert not result["refused"]
    assert result["answer_validation"]["status"] == "not_assessed"
    assert result["answerability"] == "not_assessed"

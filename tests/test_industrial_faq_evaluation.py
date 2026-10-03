"""The report must expose quality failures, and the demo must stay isolated."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("industrial_faq_eval", ROOT / "scripts/evaluate_industrial_faq.py")
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


def response(*, refused=False, text="合成电压为 24 V DC", cited="合成电压为 24 V DC"):
    hit = {"document_id": 7, "content": cited}
    return {"retrieval_candidates": [hit], "citations": [] if refused else [hit],
            "answer": text, "refused": refused, "refusal_reason": "insufficient_evidence" if refused else "",
            "citation_check": {"valid": True}, "evidence_health": {}}


CASE = {"id": "voltage", "expected_document_keys": ["spec"],
        "required_fact_phrases": ["24 V DC"], "should_refuse": False}


def test_present_citation_id_is_not_enough_for_labelled_fact_support():
    measured = evaluator.score_case(CASE, response(text="合成电压为 48 V DC"), {"spec": {"document_id": 7}}, 1.23)
    assert measured["candidate_document_recall_at_k"] == 1
    assert measured["labelled_fact_support"] is False
    assert measured["answer_missing_labelled_facts"] and measured["bad_case"]
    assert measured["phrase_checks"][0] == {"phrase": "24 V DC", "in_answer": False, "in_cited_excerpt": True}


def test_matching_answer_without_matching_evidence_is_not_supported():
    measured = evaluator.score_case(CASE, response(cited="没有说明电压"), {"spec": {"document_id": 7}}, 2)
    assert measured["labelled_fact_support"] is False and measured["bad_case"]


def test_false_refusal_retains_candidate_recall_but_not_adopted_recall():
    measured = evaluator.score_case(CASE, response(refused=True, text="证据不足"), {"spec": {"document_id": 7}}, 3)
    assert measured["false_refusal"] and measured["bad_case"]
    assert measured["candidate_document_recall_at_k"] == 1
    assert measured["adopted_document_recall"] == 0


def test_irrelevant_excerpt_is_false_accept_without_inventing_fabrication_metric():
    case = {**CASE, "should_refuse": True, "required_fact_phrases": [], "expected_document_keys": []}
    measured = evaluator.score_case(case, response(), {}, 4)
    assert measured["false_accept"] and measured["bad_case"]
    assert measured["labelled_fact_support"] is None
    assert measured["candidate_document_recall_at_k"] is None
    assert measured["cost"]["provider_invoice_amount"] is None
    assert measured["cost"]["model_tokens"] is None


def test_cli_uses_disposable_database_and_preserves_full_bad_case_report(tmp_path):
    # Poison incoming configuration: a safe evaluation must override it before
    # importing the application, even if the parent has configured online models.
    forbidden_db = tmp_path / "must-not-create.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{forbidden_db}", "SAAS_MODE": "true",
           "DEFAULT_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "synthetic-never-use-this-key",
           "RAG_RETRIEVAL_MODE": "semantic", "RAG_VECTOR_PATH": str(tmp_path / "must-not-create-index"),
           "LANGSMITH_TRACING": "true", "LANGSMITH_TRACING_V2": "true", "LANGCHAIN_TRACING_V2": "true",
           "LANGCHAIN_TRACING": "true", "LANGCHAIN_HANDLER": "langchain",
           "LANGSMITH_API_KEY": "synthetic-tracing-key", "LANGCHAIN_API_KEY": "synthetic-tracing-key",
           "LANGSMITH_ENDPOINT": "http://127.0.0.1:9", "LANGCHAIN_ENDPOINT": "http://127.0.0.1:9"}
    output = tmp_path / "result.json"
    command = [sys.executable, str(ROOT / "scripts/evaluate_industrial_faq.py"), "--output", str(output)]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not forbidden_db.exists() and not (tmp_path / "must-not-create-index").exists()
    serialized = output.read_text(encoding="utf-8")
    assert "synthetic-never-use-this-key" not in serialized + result.stdout + result.stderr
    assert "synthetic-tracing-key" not in serialized + result.stdout + result.stderr
    assert "Failed to send" not in result.stderr
    report = json.loads(serialized)
    assert report["workflow_checks_passed"] and report["permission_checks"]["passed"]
    assert report["generation"]["is_llm"] is False
    assert report["real_authorized_sample_count"] == 0
    assert len(report["cases"]) == report["counts"]["samples"] == 10
    assert len(report["structured_contract_cases"]) == 4
    assert report["structured_contract_counts"]["bad_case"] == 0
    assert report["bad_case_ids"] == [item["id"] for item in report["cases"] if item["bad_case"]]
    assert all("answer" in item and "retrieval_candidates" in item and "citations" in item for item in report["cases"])
    assert output.with_suffix(".delivery.md").read_text(encoding="utf-8") == report["workflow"]["delivery"]["markdown"]
    assert report["cost"]["human_work_minutes"] is None
    assert report["cost"]["provider_invoice_amount"] is None

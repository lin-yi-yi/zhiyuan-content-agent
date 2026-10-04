"""Frozen partition, honest metrics and isolated CLI contracts; no semantic mocks as evidence."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("frozen_faq_eval", ROOT / "scripts/evaluate_frozen_faq.py")
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


def bundle():
    manifest, documents, cases = evaluator.load_frozen_dataset()
    return manifest, {"dataset_id": manifest["dataset_id"], "documents": documents}, {"dataset_id": manifest["dataset_id"], "cases": cases}


def test_frozen_bundle_has_disjoint_families_products_and_all_categories_per_split():
    manifest, documents, cases = evaluator.load_frozen_dataset()
    assert len(documents) == 14 and len(cases) == 20
    families = {}
    products = {}
    for split in ("development", "reserved"):
        selected = [case for case in cases if case["split"] == split]
        assert len(selected) == 10
        assert {case["category"] for case in selected} == set(manifest["categories"])
        families[split] = {case["scenario_family"] for case in selected}
        products[split] = {citation["product_model"] for doc in documents if doc["split"] == split for citation in doc["citations"]}
        assert len(families[split]) == 5
    assert families["development"].isdisjoint(families["reserved"])
    assert products["development"].isdisjoint(products["reserved"])
    assert manifest["annotation_review_status"] == "pending_human_review"
    assert manifest["unseen_status"] == "not_claimed" and manifest["real_authorized_sample_count"] == 0


@pytest.mark.parametrize("name", ["corpus.json", "cases.json", "manifest.json"])
def test_any_byte_change_invalidates_frozen_bundle(tmp_path, name):
    directory = tmp_path / "bundle"
    shutil.copytree(evaluator.DATASET, directory)
    path = directory / name
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(evaluator.EvaluationError, match="哈希"):
        evaluator.load_frozen_dataset(directory)


def test_rehashing_modified_questions_does_not_silently_replace_registered_version(tmp_path):
    directory = tmp_path / "bundle"
    shutil.copytree(evaluator.DATASET, directory)
    questions = directory / "cases.json"
    questions.write_bytes(questions.read_bytes() + b"\n")
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["files"]["cases.json"] = hashlib.sha256(questions.read_bytes()).hexdigest()
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
    with pytest.raises(evaluator.EvaluationError, match="清单哈希"):
        evaluator.load_frozen_dataset(directory)


@pytest.mark.parametrize("mutation", ["family_split", "product_split", "duplicate_question", "wrong_scope", "labels_reviewed",
                                      "blind_claim", "config_changed", "duplicate_id", "wrong_missing_fact"])
def test_partition_and_provenance_contracts_fail_closed(mutation):
    manifest, corpus, questions = deepcopy(bundle())
    docs, cases = corpus["documents"], questions["cases"]
    if mutation == "family_split":
        docs[-1]["scenario_family"] = docs[0]["scenario_family"]
    elif mutation == "product_split":
        docs[-1]["citations"][0]["product_model"] = docs[0]["citations"][0]["product_model"]
    elif mutation == "duplicate_question":
        cases[-1]["question"] = "  " + cases[0]["question"] + "\n"
    elif mutation == "wrong_scope":
        cases[0]["expected_document_keys"] = [docs[-1]["key"]]
    elif mutation == "labels_reviewed":
        manifest["annotation_review_status"] = "human_reviewed"
    elif mutation == "blind_claim":
        manifest["unseen_status"] = "unseen"
    elif mutation == "config_changed":
        manifest["configuration"]["semantic_min_score"] = 0.1
    elif mutation == "duplicate_id":
        cases[-1]["id"] = cases[0]["id"]
    else:
        cases[0]["expected_missing_facts"] = [{"product_model": "unrequested", "parameter": "price"}]
    with pytest.raises(evaluator.EvaluationError):
        evaluator.validate_contract(manifest, corpus, questions)


def response(*, refused=False, answer="合成电压 24 V", excerpt="合成电压 24 V", reason="", missing=None):
    hit = {"document_id": 7, "content": excerpt}
    return {"retrieval_candidates": [hit], "citations": [] if refused else [hit], "answer": answer,
            "refused": refused, "refusal_reason": reason, "citation_check": {"valid": True}, "evidence_health": {},
            "missing_facts": missing or [], "answerability": "not_assessed"}


CASE = {"id": "metric-example", "scenario_family": "unit", "category": "paraphrase", "split": "development",
        "question": "synthetic voltage", "expected_document_keys": ["source"], "required_fact_phrases": ["24 V"],
        "should_refuse": False, "required_facts": [], "expected_refusal_reasons": [], "expected_missing_facts": []}


def score(case, answer):
    return evaluator.score_frozen_case(case, answer, {"source": {"document_id": 7}}, 1.5)


def test_candidate_recall_does_not_hide_wrong_answer_or_false_refusal():
    wrong = score(CASE, response(answer="48 V"))
    assert wrong["candidate_document_recall_at_k"] == 1
    assert wrong["labelled_fact_support"] is False
    assert wrong["bad_case_reasons"] == ["answer_missing_labelled_facts"]
    refused = score(CASE, response(refused=True, answer="资料不足", reason="insufficient_evidence"))
    assert refused["candidate_document_recall_at_k"] == 1 and refused["adopted_document_recall"] == 0
    assert refused["bad_case_reasons"] == ["false_refusal"]


def test_missing_reason_and_exact_missing_parameters_are_scored_separately():
    fact = {"product_model": "合成设备", "parameter": "电流"}
    case = {**CASE, "should_refuse": True, "expected_document_keys": [], "required_fact_phrases": [],
            "required_facts": [fact], "expected_missing_facts": [fact], "expected_refusal_reasons": ["missing_structured_facts"]}
    wrong = score(case, response(refused=True, reason="insufficient_evidence"))
    assert set(wrong["bad_case_reasons"]) == {"unexpected_refusal_reason", "missing_facts_mismatch"}
    correct = score(case, response(refused=True, reason="missing_structured_facts", missing=[fact]))
    assert not correct["bad_case"]


def test_summaries_preserve_denominators_nulls_and_every_failure():
    rejected_case = {**CASE, "should_refuse": True, "expected_document_keys": [], "required_fact_phrases": [],
                     "expected_refusal_reasons": ["insufficient_evidence"]}
    false_accept = score(rejected_case, response())
    assert false_accept["bad_case_reasons"] == ["false_accept"]
    summary = evaluator.summarize([false_accept])
    assert summary["false_accept_rate"] == {"numerator": 1, "denominator": 1, "value": 1.0}
    assert summary["false_refusal_rate"] == {"numerator": 0, "denominator": 0, "value": None}
    assert summary["candidate_document_recall_at_k"]["value"] is None
    assert summary["labelled_phrase_support_rate"]["value"] is None
    assert summary["bad_case_count"] == 1


def cli(output, *args, env=None):
    return subprocess.run([sys.executable, str(ROOT / "scripts/evaluate_frozen_faq.py"), "--output", str(output), *map(str, args)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)


@pytest.mark.parametrize("split", ["development", "reserved"])
def test_real_lexical_cli_isolates_environment_and_retains_all_selected_cases(tmp_path, split):
    forbidden_db = tmp_path / "never-open.db"
    forbidden_vectors = tmp_path / "never-open-vectors"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{forbidden_db}", "RAG_VECTOR_PATH": str(forbidden_vectors),
           "SAAS_MODE": "true", "DEFAULT_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "synthetic-private-key",
           "RAG_RETRIEVAL_MODE": "semantic", "RAG_EMBEDDING_PROVIDER": "openai", "RAG_EMBEDDING_API_KEY": "synthetic-embedding-key",
           "RAG_EMBEDDING_BASE_URL": "https://example.com/never-call", "RAG_SEMANTIC_MIN_SCORE": "0.01",
           "LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true", "LANGSMITH_API_KEY": "synthetic-tracing-key",
           "LANGCHAIN_API_KEY": "synthetic-tracing-key", "LANGSMITH_ENDPOINT": "http://127.0.0.1:9", "MODEL_PRICING_JSON": "invalid"}
    output = tmp_path / "result.json"
    args = [] if split == "development" else ["--split", split]
    result = cli(output, *args, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report["completed"] and report["split"] == split and report["mode"] == "lexical"
    assert len(report["cases"]) == report["summary"]["sample_count"] == 10
    assert {case["split"] for case in report["cases"]} == {split}
    assert report["bad_case_ids"] == [case["id"] for case in report["cases"] if case["bad_case"]]
    assert report["summary"]["bad_case_count"] == len(report["bad_case_ids"])
    assert all("answer" in case and "retrieval_candidates" in case and "citations" in case for case in report["cases"])
    assert report["configuration"]["semantic_min_score"] == 0.55
    assert evaluator.digest(evaluator.canonical(report["configuration"])) == report["configuration_sha256"]
    assert evaluator.digest(evaluator.canonical(report["runtime_configuration"])) == report["runtime_configuration_sha256"]
    assert report["outgoing_connection_attempts"] == 0 and report["external_generation_requests"] == 0
    assert not report["generation_quality_evaluated"] and not report["embedding"]["real_embedding_inference"]
    assert report["annotation_review_status"] == "pending_human_review" and report["unseen_status"] == "not_claimed"
    assert report["tuning_exclusion_independently_verified"] is False and report["real_authorized_sample_count"] == 0
    assert len(report["fault_injections"]) == 1 and report["fault_injections"][0]["normal_verification_was_blocked"]
    assert report["cost"]["provider_invoice_amount"] is None and report["cost"]["model_tokens"] is None
    serialized = output.read_text() + result.stdout + result.stderr
    assert all(secret not in serialized for secret in ("synthetic-private-key", "synthetic-embedding-key", "synthetic-tracing-key"))
    assert not forbidden_db.exists() and not forbidden_vectors.exists()


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_semantic_modes_need_explicit_existing_cache_and_never_fake_success(tmp_path, mode):
    output = tmp_path / "not-run.json"
    result = cli(output, "--mode", mode)
    assert result.returncode == 2
    report = json.loads(output.read_text())
    assert report["completed"] is False and report["cases"] == []
    assert report["outgoing_connection_attempts"] == 0 and "--cache-dir" in report["error"]["message"]
    assert "summary" not in report


def test_empty_real_cache_does_not_download_or_fall_back_to_mock_vectors(tmp_path):
    cache = tmp_path / "empty-cache"
    cache.mkdir()
    output = tmp_path / "failed.json"
    result = cli(output, "--mode", "semantic", "--cache-dir", cache)
    assert result.returncode == 2
    report = json.loads(output.read_text())
    assert not report["completed"] and report["cases"] == [] and report["outgoing_connection_attempts"] == 0
    assert "缓存模型" in report["error"]["message"] and "未使用模拟向量" in report["error"]["message"]
    assert "embedding" not in report and "summary" not in report
    assert list(cache.iterdir()) == []


def test_model_cache_copy_keeps_source_read_only_and_follows_only_internal_file_links(tmp_path):
    source = tmp_path / "cache"
    repo = source / "models--Qdrant--bge-small-zh-v1.5"
    repo.mkdir(parents=True)
    blob = source / "model-blob"
    blob.write_bytes(b"synthetic copy-boundary test, not a real embedding model")
    (repo / "model.onnx").symlink_to(blob)
    target = evaluator.copy_model_cache(source, tmp_path / "copy")
    copied = target / repo.name / "model.onnx"
    assert not copied.is_symlink() and copied.read_bytes() == blob.read_bytes()
    copied.write_bytes(b"library cache mutation")
    assert blob.read_bytes() == b"synthetic copy-boundary test, not a real embedding model"
    (repo / "model.onnx").unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"synthetic outside cache")
    (repo / "model.onnx").symlink_to(outside)
    with pytest.raises(evaluator.EvaluationError, match="越界"):
        evaluator.copy_model_cache(source, tmp_path / "other-copy")


def test_git_failure_does_not_claim_clean_worktree(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(evaluator.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout=""))
    result = evaluator.code_identity()
    assert result["git_commit"] is None and result["worktree_dirty"] is None


def test_cli_preserves_existing_reports_and_rejects_mutated_dataset(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("synthetic previous report")
    result = cli(output)
    assert result.returncode == 2 and output.read_text() == "synthetic previous report"
    directory = tmp_path / "dataset"
    shutil.copytree(evaluator.DATASET, directory)
    path = directory / "cases.json"
    path.write_bytes(path.read_bytes() + b"\n")
    failed = tmp_path / "failed.json"
    result = cli(failed, "--dataset-dir", directory)
    assert result.returncode == 2
    report = json.loads(failed.read_text())
    assert not report["completed"] and report["cases"] == []
    assert "哈希" in report["error"]["message"]

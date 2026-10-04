"""Question-only inference, annotation separation, honest coverage and Unicode metrics."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("question_scope_evaluation", ROOT / "scripts/evaluate_question_scope.py")
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


@pytest.fixture(autouse=True)
def isolate_evaluator_environment():
    # configure_offline is intentionally process-wide in the CLI; restore it in pytest.
    with patch.dict(os.environ, os.environ.copy(), clear=True):
        yield


def bundle():
    manifest, cases = evaluator.load_inputs()
    return manifest, cases, evaluator.load_annotations(evaluator.DATASET, manifest, cases)


def span(query, value):
    start = query.index(value)
    return {"start": start, "end": start + len(value), "text": value}


def proposal(query, pairs=(), *, issue=None):
    return {"schema_version": 1, "query_hash": evaluator.digest(query.encode()), "method": "rules",
            "parser_version": "rules_v1", "offset_unit": "unicode_codepoint", "provider": None, "model": None,
            "status": "needs_clarification" if issue else "proposed",
            "candidates": [{"product_model": p, "parameter": a, "product_span": span(query, p),
                            "parameter_span": span(query, a)} for p, a in pairs],
            "issues": [{"code": issue, "span": span(query, query)}] if issue else [],
            "requires_confirmation": True, "requirements_complete": False}


def score(case, label, response, error=None):
    return evaluator.score_case(case, label, {"response": response, "error": error, "latency_ms": 0.25})


def test_separate_versioned_dataset_is_synthetic_not_blind_and_has_diverse_queries():
    manifest, cases, labels = bundle()
    assert len(cases) == len(labels) == 32
    assert manifest["sample_origin"] == "synthetic"
    assert manifest["annotation_review_status"] == "pending_human_review"
    assert manifest["unseen_status"] == "not_claimed"
    assert manifest["evaluation_role"] == "development"
    assert manifest["real_authorized_sample_count"] == 0
    assert {case["category"] for case in cases} >= {"unknown_model", "unknown_parameter", "compound", "multi_model",
                                                   "negation", "ambiguity", "unicode", "injection", "limit"}
    assert all(set(case) == {"id", "category", "query"} for case in cases)
    assert sum(label["grammar_scope"] == "outside_rules_v1" for label in labels.values()) == 2


@pytest.mark.parametrize("name", ["inputs.json", "annotations.json", "manifest.json"])
def test_hash_manifest_rejects_any_changed_file(tmp_path, name):
    folder = tmp_path / "dataset"
    shutil.copytree(evaluator.DATASET, folder)
    path = folder / name
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(evaluator.EvaluationError, match="哈希"):
        manifest, cases = evaluator.load_inputs(folder)
        evaluator.load_annotations(folder, manifest, cases)


def test_rehashing_fixture_cannot_silently_replace_v1(tmp_path):
    folder = tmp_path / "dataset"
    shutil.copytree(evaluator.DATASET, folder)
    path = folder / "inputs.json"
    path.write_bytes(path.read_bytes() + b"\n")
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["files"]["inputs.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(evaluator.EvaluationError, match="哈希"):
        evaluator.load_inputs(folder)


def test_inputs_load_without_annotations_and_inference_receives_only_raw_question(tmp_path):
    folder = tmp_path / "dataset"
    shutil.copytree(evaluator.DATASET, folder)
    (folder / "annotations.json").unlink()
    _, cases = evaluator.load_inputs(folder)
    calls = []

    def parser(query, **kwargs):
        calls.append((query, kwargs))
        return proposal(query, issue="unsupported_question")

    records = evaluator.collect_proposals(cases, parser, folder)
    assert len(records) == 32
    assert calls == [(case["query"], {"method": "rules"}) for case in cases]


@pytest.mark.parametrize("path", [evaluator.DATASET / "annotations.json", evaluator.DATASET / "inputs.json",
                                  ROOT / "scripts/fixtures/faq_frozen_v1/cases.json", ROOT / ".env",
                                  ROOT / ".data/never-read.db"])
def test_inference_guard_denies_fixture_dotenv_and_existing_data_reads(path):
    with evaluator.inference_boundary():
        with pytest.raises(evaluator.EvaluationError, match="禁止读取"):
            path.read_bytes()


def test_inference_guard_blocks_network_and_sanitizes_parser_exceptions():
    case = {"id": "guard", "category": "test", "query": "合成 AB-2的电压是多少？"}

    def parser(query, **kwargs):
        with pytest.raises(evaluator.EvaluationError, match="禁止网络"):
            socket.create_connection(("example.invalid", 443))
        raise RuntimeError("SECRET_CREDENTIAL_DO_NOT_ECHO")

    records = evaluator.collect_proposals([case], parser)
    assert records[0]["error"] == "parser_failed"
    assert "SECRET" not in json.dumps(records)


def test_annotation_read_occurs_after_all_predictions_and_changes_only_scores(monkeypatch):
    _, cases, labels = bundle()
    events = []
    read = evaluator.load_annotations

    def parser(query, **kwargs):
        events.append("inference")
        return proposal(query, issue="unsupported_question")

    def after_inference(*args):
        assert events == ["inference"] * len(cases)
        events.append("labels")
        return read(*args)

    monkeypatch.setattr(evaluator, "load_annotations", after_inference)
    monkeypatch.setattr(evaluator, "configure_offline", lambda: None)
    report = evaluator.evaluate(parser=parser)
    assert report["annotations_loaded_after_inference"] is True
    assert events[-1] == "labels"
    case = cases[0]
    response = proposal(case["query"], [("合成岚屿 LY-40", "电压")])
    changed_label = {**labels[case["id"]], "expected_pairs": [{"product_model": "CANARY_LABEL_ONLY", "parameter": "DO_NOT_INFER"}]}
    assert not score(case, labels[case["id"]], response)["bad_case"]
    assert score(case, changed_label, response)["bad_case"]
    assert "CANARY_LABEL_ONLY" not in json.dumps(response)


def test_always_clarify_cannot_earn_clear_question_success():
    _, cases, labels = bundle()
    results = [score(case, labels[case["id"]], proposal(case["query"], issue="unsupported_question")) for case in cases]
    summary = evaluator.summarize(results)
    assert summary["false_clarification_rate"] == {"numerator": 19, "denominator": 19, "value": 1.0}
    assert summary["clear_question_success_rate"]["value"] == 0
    assert summary["exact_pair_match_on_clear_questions"]["value"] == 0
    assert summary["bad_case_count"] >= 19


def test_compound_omission_and_wrong_model_pairs_remain_visible():
    _, cases, labels = bundle()
    case = next(case for case in cases if case["id"] == "qs-09")
    response = proposal(case["query"], [("合成岚屿 LY-40", "电压")])
    result = score(case, labels[case["id"]], response)
    assert result["missing_pairs"] == [{"product_model": "合成岚屿 LY-40", "parameter": "单价"}]
    assert "missing_pairs" in result["bad_case_reasons"]
    multi = next(case for case in cases if case["id"] == "qs-12")
    wrong = proposal(multi["query"], [("合成岚屿 LY-40", "电压"), ("合成岚屿 LY-40", "流量")])
    result = score(multi, labels[multi["id"]], wrong)
    assert result["contract_valid"]  # Every word exists; binding can still be wrong.
    assert set(result["bad_case_reasons"]) == {"missing_pairs", "invented_pairs"}


def test_ambiguous_case_pair_metric_is_not_invented_and_empty_denominators_are_null():
    _, cases, labels = bundle()
    case = next(case for case in cases if case["id"] == "qs-14")
    result = score(case, labels[case["id"]], proposal(case["query"], issue="ambiguous_binding"))
    assert result["pair_exact_match"] is None and not result["bad_case"]
    summary = evaluator.summarize([result])
    assert summary["exact_pair_match_on_clear_questions"] == {"numerator": 0, "denominator": 0, "value": None}


def test_unicode_codepoint_spans_reject_utf16_offsets_and_normalization():
    query = "型号：合成🔬设备 AB-1；参数：电压"
    response = proposal(query, [("合成🔬设备 AB-1", "电压")])
    assert evaluator.response_errors(query, response) == []
    response["candidates"][0]["parameter_span"]["start"] += 1
    response["candidates"][0]["parameter_span"]["end"] += 1
    assert "invalid_original_span" in evaluator.response_errors(query, response)
    query = "型号：合成 ＡＢ－１；参数：电压"
    response = proposal(query, [("合成 ＡＢ－１", "电压")])
    response["candidates"][0]["product_model"] = "合成 AB-1"
    assert "invalid_original_span" in evaluator.response_errors(query, response)


@pytest.mark.parametrize("mutation", ["version_bool", "unknown_field", "status_object", "span_bool", "candidates_object",
                                      "confirmation_false", "complete_true", "wrong_hash", "model_identity", "duplicate"])
def test_invalid_contracts_cannot_earn_credit(mutation):
    _, cases, labels = bundle()
    case = cases[0]
    response = proposal(case["query"], [("合成岚屿 LY-40", "电压")])
    if mutation == "version_bool":
        response["schema_version"] = True
    elif mutation == "unknown_field":
        response["answer"] = "DO_NOT_ECHO"
    elif mutation == "status_object":
        response["status"] = {"raw": "DO_NOT_ECHO"}
    elif mutation == "span_bool":
        response["candidates"][0]["product_span"]["start"] = False
    elif mutation == "candidates_object":
        response["candidates"] = {"raw": "DO_NOT_ECHO"}
    elif mutation == "confirmation_false":
        response["requires_confirmation"] = False
    elif mutation == "complete_true":
        response["requirements_complete"] = True
    elif mutation == "wrong_hash":
        response["query_hash"] = "0" * 64
    elif mutation == "model_identity":
        response["provider"] = "DO_NOT_ECHO"
    else:
        response["candidates"].append(deepcopy(response["candidates"][0]))
    result = score(case, labels[case["id"]], response)
    assert result["bad_case"] and not result["pair_exact_match"]
    assert "DO_NOT_ECHO" not in json.dumps(result)


def test_real_rules_preserve_unknown_labels_and_unicode_without_annotations():
    evaluator.configure_offline()
    _, cases = evaluator.load_inputs()
    chosen = [case for case in cases if case["category"] in {"unknown_model", "unknown_parameter", "unicode"}]
    records = evaluator.collect_proposals(chosen)
    # Read labels only after the actual pure parser has produced every response.
    _, _, labels = bundle()
    for case, record in zip(chosen, records, strict=True):
        result = evaluator.score_case(case, labels[case["id"]], record)
        assert not result["bad_case"], result


def test_cli_has_no_paid_model_switch_and_preserves_bad_cases_and_isolation(tmp_path):
    output = tmp_path / "report.json"
    forbidden = tmp_path / "never-open.db"
    vectors = tmp_path / "never-open-vectors"
    diagnostics = tmp_path / "never-open-diagnostics"
    env = dict(os.environ, PYTHON_DOTENV_DISABLED="0", DATABASE_URL=f"sqlite:///{forbidden}",
               RAG_VECTOR_PATH=str(vectors), DIAGNOSTIC_LOG_DIR=str(diagnostics), SAAS_MODE="true",
               DEFAULT_LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="SYNTHETIC_SECRET_CANARY")
    command = [sys.executable, str(ROOT / "scripts/evaluate_question_scope.py"), "--output", str(output)]
    run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(output.read_text())
    assert report["evaluation_completed"] and report["method"] == "rules"
    assert len(report["cases"]) == 32
    assert len(report["bad_cases"]) == report["summary"]["bad_case_count"]
    assert report["bad_cases"] == [case for case in report["cases"] if case["bad_case"]]
    assert {"qs-31", "qs-32"} <= {case["id"] for case in report["bad_cases"]}
    assert report["quality_passed"] is False
    assert report["all_response_contracts_passed"] is True
    assert report["supported_grammar_passed"] is True
    assert report["by_grammar_scope"]["supported"]["bad_case_count"] == 0
    assert report["by_grammar_scope"]["supported"]["case_count"] == 30
    assert report["summary"]["unsafe_proposal_rate"]["numerator"] == 0
    assert report["usage"]["model_calls"] == 0 and report["usage"]["provider_cost"] is None
    assert report["usage"]["local_compute_cost"] is None and report["usage"]["tokens"] is None
    assert report["model_quality_evaluated"] is False and report["confirmation_executed"] is False
    assert not forbidden.exists() and not vectors.exists() and not diagnostics.exists()
    assert "SYNTHETIC_SECRET_CANARY" not in output.read_text() + run.stdout + run.stderr
    fail = subprocess.run(command + ["--fail-on-bad-cases"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert fail.returncode == 2 and json.loads(output.read_text())["evaluation_completed"]
    help_run = subprocess.run(command[:2] + ["--help"], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert help_run.returncode == 0 and "--provider" not in help_run.stdout


def test_failure_replaces_old_success_report_without_exception_payload(tmp_path, monkeypatch, capsys):
    output = tmp_path / "report.json"
    output.write_text('{"evaluation_completed":true}')

    def fail():
        raise RuntimeError("SECRET_EXCEPTION_PAYLOAD")

    monkeypatch.setattr(evaluator, "evaluate", fail)
    assert evaluator.main(["--output", str(output)]) == 1
    result = json.loads(output.read_text())
    assert result["evaluation_completed"] is False and result["quality_passed"] is None
    assert "SECRET_EXCEPTION_PAYLOAD" not in output.read_text() + capsys.readouterr().out

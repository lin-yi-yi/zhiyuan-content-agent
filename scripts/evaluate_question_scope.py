#!/usr/bin/env python3
"""Offline question-only proposal evaluation, with annotations joined after inference."""
import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "scripts/fixtures/question_scope_v1"
MANIFEST_SHA256 = "c3305ca4b3584aac01166dba49d2d03f3653d3fa22180a7c1b0f7d063ab016ae"
ISSUE_CODES = {"missing_product", "missing_parameter", "ambiguous_binding", "negation",
               "unsupported_question", "too_many_requirements"}
RESPONSE_KEYS = {"schema_version", "query_hash", "method", "parser_version", "offset_unit", "provider",
                 "model", "status", "candidates", "issues", "requires_confirmation", "requirements_complete"}


class EvaluationError(ValueError):
    """Sanitized evaluation failure; never print arbitrary parser exception text."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise EvaluationError(message)


def read_checked(directory, name, expected):
    try:
        raw = (Path(directory) / name).read_bytes()
        require(digest(raw) == expected, "题集文件哈希不匹配；修改问题或标注须发布新版本。")
        return json.loads(raw)
    except (OSError, ValueError) as exc:
        if isinstance(exc, EvaluationError):
            raise
        raise EvaluationError("题集文件不可读或格式无效。") from None


def load_inputs(directory=DATASET):
    """Load only manifest and unlabelled queries; do not read annotations here."""
    manifest = read_checked(directory, "manifest.json", MANIFEST_SHA256)
    require(manifest.get("schema_version") == 1 and manifest.get("dataset_id") == "question_scope_v1",
            "题集版本不受支持。")
    require(manifest.get("sample_origin") == "synthetic"
            and manifest.get("annotation_author") == "development_assistant"
            and manifest.get("annotation_review_status") == "pending_human_review"
            and manifest.get("unseen_status") == "not_claimed"
            and manifest.get("evaluation_role") == "development"
            and manifest.get("real_authorized_sample_count") == 0,
            "合成来源、待复核和非盲测声明不能提升。")
    payload = read_checked(directory, "inputs.json", manifest["files"]["inputs.json"])
    cases = payload.get("cases")
    require(payload.get("dataset_id") == manifest["dataset_id"] and isinstance(cases, list)
            and len(cases) == manifest["case_count"] and 24 <= len(cases) <= 100, "问题数量或题集标识无效。")
    ids, queries = set(), set()
    for case in cases:
        require(isinstance(case, dict) and set(case) == {"id", "category", "query"}, "问题输入不得携带标注。")
        require(isinstance(case["id"], str) and case["id"] and case["id"] not in ids, "问题编号重复或无效。")
        require(isinstance(case["query"], str) and 0 < len(case["query"]) <= 1000
                and case["query"] not in queries and case["category"] in manifest["categories"], "问题正文或分类无效。")
        ids.add(case["id"])
        queries.add(case["query"])
    return manifest, cases


def load_annotations(directory, manifest, cases):
    """Called only after every proposal is collected; these labels are scorer inputs."""
    payload = read_checked(directory, "annotations.json", manifest["files"]["annotations.json"])
    labels = payload.get("annotations")
    require(payload.get("dataset_id") == manifest["dataset_id"] and isinstance(labels, list)
            and len(labels) == len(cases), "标注数量或标识无效。")
    by_id = {}
    queries = {case["id"]: case["query"] for case in cases}
    for label in labels:
        key = label.get("id")
        require(key in queries and key not in by_id, "标注编号重复或无效。")
        require(label.get("expected_status") in {"proposed", "needs_clarification"}
                and label.get("grammar_scope") in {"supported", "outside_rules_v1"}, "标注状态无效。")
        pairs = label.get("expected_pairs")
        if label["expected_status"] == "proposed":
            require(isinstance(pairs, list) and 0 < len(pairs) <= 10, "明确问题必须有范围标注。")
            seen = set()
            for pair in pairs:
                require(isinstance(pair, dict) and set(pair) == {"product_model", "parameter"}
                        and all(isinstance(value, str) and value and value in queries[key] for value in pair.values()),
                        "范围标注必须是问题原文，不能加入目录规范名或答案。")
                item = (pair["product_model"], pair["parameter"])
                require(item not in seen, "范围标注重复。")
                seen.add(item)
            require(label.get("acceptable_issue_codes") == [], "明确问题不应预设澄清理由。")
        else:
            require(pairs is None and isinstance(label.get("acceptable_issue_codes"), list)
                    and label["acceptable_issue_codes"]
                    and set(label["acceptable_issue_codes"]) <= ISSUE_CODES, "澄清问题的标注无效。")
        by_id[key] = label
    return by_id


def configure_offline():
    # Run before application imports. No service, database, or retrieval is started.
    os.environ.update({"PYTHON_DOTENV_DISABLED": "1", "SAAS_MODE": "false", "APP_ENV": "local",
                       "DATABASE_URL": "sqlite:///:memory:", "RAG_RETRIEVAL_MODE": "lexical",
                       "DIAGNOSTIC_LOG_DIR": "", "AIHOT_ENABLED": "false", "GITHUB_ENABLED": "false"})
    for namespace in ("DEEPSEEK", "QWEN", "DOUBAO", "KIMI", "OPENAI", "RAG_EMBEDDING"):
        os.environ[f"{namespace}_API_KEY"] = ""
    for namespace in ("LANGSMITH", "LANGCHAIN"):
        os.environ[f"{namespace}_API_KEY"] = ""
        os.environ[f"{namespace}_TRACING"] = "false"
        os.environ[f"{namespace}_TRACING_V2"] = "false"
    os.environ.pop("LANGCHAIN_HANDLER", None)
    for task in ("DEFAULT_LLM", "TOPIC_SCORE", "DRAFT_GENERATION", "CARD_GENERATION", "COMPLIANCE_CHECK"):
        os.environ[f"{task}_PROVIDER"] = "local"
        os.environ[f"{task}_MODEL"] = "local-rule-based-v0"


@contextmanager
def inference_boundary(directory=DATASET):
    """Regression guard, not a hostile-code sandbox: deny fixture/env reads and network."""
    blocked = (Path(directory).resolve(), (ROOT / "scripts/fixtures").resolve(), (ROOT / ".data").resolve())

    def guard_open(original):
        def guarded(file, *args, **kwargs):
            if not isinstance(file, int):
                try:
                    path = Path(os.fsdecode(file)).resolve()
                    denied = path.name == ".env" or path.name.startswith(".env.")
                    denied = denied or any(path == base or base in path.parents for base in blocked)
                    require(not denied, "推理阶段禁止读取环境文件、评测数据或现有运行数据。")
                except TypeError:
                    pass
            return original(file, *args, **kwargs)
        return guarded

    def no_network(*args, **kwargs):
        raise EvaluationError("离线评测禁止网络调用。")

    with ExitStack() as stack:
        for target, original in (("builtins.open", open), ("io.open", io.open), ("os.open", os.open)):
            stack.enter_context(patch(target, guard_open(original)))
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect", "socket.socket.connect_ex"):
            stack.enter_context(patch(target, no_network))
        yield


def collect_proposals(cases, parser=None, directory=DATASET):
    """Only query + fixed rules method reach production. No annotations/catalog argument."""
    records = []
    with inference_boundary(directory):
        if parser is None:
            sys.path.insert(0, str(ROOT / "backend"))
            from app.agent_core.question_scope import propose_question_scope
            parser = propose_question_scope
        for case in cases:
            started = perf_counter()
            try:
                response = parser(case["query"], method="rules")
                error = None
            except Exception:
                response, error = None, "parser_failed"
            records.append({"id": case["id"], "response": response, "error": error,
                            "latency_ms": round((perf_counter() - started) * 1000, 3)})
    return records


def valid_span(query, span):
    return (isinstance(span, dict) and set(span) == {"start", "end", "text"}
            and type(span["start"]) is int and type(span["end"]) is int
            and 0 <= span["start"] < span["end"] <= len(query)
            and isinstance(span["text"], str) and query[span["start"]:span["end"]] == span["text"])


def _response_errors(query, response):
    if not isinstance(response, dict) or set(response) != RESPONSE_KEYS:
        return ["response_schema"]
    errors = []
    if (type(response["schema_version"]) is not int or response["schema_version"] != 1
            or response["query_hash"] != digest(query.encode()) or response["method"] != "rules"
            or response["parser_version"] != "rules_v1" or response["offset_unit"] != "unicode_codepoint"
            or response["status"] not in {"proposed", "needs_clarification"}):
        errors.append("response_metadata")
    if response["requires_confirmation"] is not True or response["requirements_complete"] is not False:
        errors.append("confirmation_contract")
    if response["provider"] is not None or response["model"] is not None:
        errors.append("unexpected_model_identity")
    if not isinstance(response["candidates"], list) or len(response["candidates"]) > 10:
        errors.append("candidate_schema")
    else:
        for candidate in response["candidates"]:
            if not isinstance(candidate, dict) or set(candidate) != {"product_model", "parameter", "product_span", "parameter_span"}:
                errors.append("candidate_schema")
                continue
            for label, field in (("product_model", "product_span"), ("parameter", "parameter_span")):
                if (not valid_span(query, candidate[field]) or not isinstance(candidate[label], str)
                        or not 0 < len(candidate[label]) <= 200 or candidate[label] != candidate[field].get("text")):
                    errors.append("invalid_original_span")
    if not isinstance(response["issues"], list):
        errors.append("issue_schema")
    else:
        for issue in response["issues"]:
            if (not isinstance(issue, dict) or set(issue) != {"code", "span"}
                    or issue["code"] not in ISSUE_CODES or not valid_span(query, issue["span"])):
                errors.append("issue_schema")
        if response["status"] == "needs_clarification" and not response["issues"]:
            errors.append("clarification_without_issue")
        if response["status"] == "proposed" and (response["issues"] or not response["candidates"]):
            errors.append("proposal_without_clean_candidates")
    return sorted(set(errors))


def response_errors(query, response):
    try:
        return _response_errors(query, response)
    except (TypeError, KeyError, AttributeError):
        return ["response_schema"]


def pair_list(pairs):
    return [{"product_model": product, "parameter": parameter} for product, parameter in sorted(pairs)]


def score_case(case, annotation, record):
    response = record["response"]
    contract_errors = [record["error"]] if record["error"] else response_errors(case["query"], response)
    expected_clear = annotation["expected_status"] == "proposed"
    actual_pairs, duplicates = set(), 0
    raw_status = response.get("status") if isinstance(response, dict) else None
    status = raw_status if isinstance(raw_status, str) and raw_status in {"proposed", "needs_clarification"} else None
    # Malformed responses cannot earn quality credit or echo arbitrary parser data.
    if not contract_errors:
        values = [(item["product_model"], item["parameter"]) for item in response["candidates"]]
        actual_pairs, duplicates = set(values), len(values) - len(set(values))
    expected_pairs = {(item["product_model"], item["parameter"]) for item in annotation["expected_pairs"] or []}
    missing = expected_pairs - actual_pairs if expected_clear else set()
    invented = actual_pairs - expected_pairs if expected_clear else set()
    issues = {item["code"] for item in response["issues"]} if not contract_errors else set()
    reasons = list(contract_errors)
    if status != annotation["expected_status"]:
        reasons.append("false_clarification" if expected_clear and status == "needs_clarification"
                       else "unsafe_proposal" if not expected_clear and status == "proposed" else "status_mismatch")
    if missing:
        reasons.append("missing_pairs")
    if invented:
        reasons.append("invented_pairs")
    if duplicates:
        reasons.append("duplicate_pairs")
    if not expected_clear and not issues.intersection(annotation["acceptable_issue_codes"]):
        reasons.append("clarification_issue_mismatch")
    return {"id": case["id"], "category": case["category"], "query": case["query"],
            "grammar_scope": annotation["grammar_scope"], "expected_status": annotation["expected_status"],
            "actual_status": status, "expected_pairs": annotation["expected_pairs"],
            "actual_pairs": pair_list(actual_pairs), "missing_pairs": pair_list(missing),
            "invented_pairs": pair_list(invented), "duplicate_pair_count": duplicates,
            "contract_valid": not contract_errors,
            "pair_exact_match": actual_pairs == expected_pairs and not contract_errors and not duplicates if expected_clear else None,
            "acceptable_issue_codes": annotation["acceptable_issue_codes"], "actual_issue_codes": sorted(issues),
            "proposal": response if not contract_errors else None,
            "latency_ms": record["latency_ms"], "bad_case": bool(reasons), "bad_case_reasons": sorted(set(reasons))}


def rate(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": round(numerator / denominator, 6) if denominator else None}


def summarize(results):
    clear = [item for item in results if item["expected_status"] == "proposed"]
    ambiguous = [item for item in results if item["expected_status"] == "needs_clarification"]
    return {"case_count": len(results), "bad_case_count": sum(item["bad_case"] for item in results),
            "contract_valid_rate": rate(sum(item["contract_valid"] for item in results), len(results)),
            "exact_pair_match_on_clear_questions": rate(sum(item["pair_exact_match"] is True for item in clear), len(clear)),
            "false_clarification_rate": rate(sum(item["actual_status"] == "needs_clarification" for item in clear), len(clear)),
            "unsafe_proposal_rate": rate(sum(item["actual_status"] == "proposed" for item in ambiguous), len(ambiguous)),
            "clear_question_success_rate": rate(sum(not item["bad_case"] for item in clear), len(clear)),
            "latency_ms": {"total": round(sum(item["latency_ms"] for item in results), 3),
                           "median": round(statistics.median(item["latency_ms"] for item in results), 3) if results else None,
                           "max": max((item["latency_ms"] for item in results), default=None)}}


def code_identity():
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                                  text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    paths = ["backend/app/agent_core/question_scope.py", "backend/app/schemas/question_scope.py",
             "scripts/evaluate_question_scope.py"]
    dirty = git("status", "--porcelain")
    return {"git_commit": git("rev-parse", "HEAD"), "working_tree_dirty": bool(dirty) if dirty is not None else None,
            "file_sha256": {name: digest((ROOT / name).read_bytes()) for name in paths if (ROOT / name).is_file()}}


def evaluate(directory=DATASET, parser=None):
    configure_offline()
    manifest, cases = load_inputs(directory)
    predictions = collect_proposals(cases, parser, directory)
    annotations = load_annotations(directory, manifest, cases)
    results = [score_case(case, annotations[case["id"]], record) for case, record in zip(cases, predictions, strict=True)]
    summary = summarize(results)
    return {"schema_version": 1, "evaluation_completed": True, "quality_passed": summary["bad_case_count"] == 0,
            "all_response_contracts_passed": all(item["contract_valid"] for item in results),
            "supported_grammar_passed": all(not item["bad_case"] for item in results if item["grammar_scope"] == "supported"),
            "created_at": datetime.now(timezone.utc).isoformat(), "dataset": manifest,
            "manifest_sha256": MANIFEST_SHA256, "implementation": code_identity(), "method": "rules",
            "inference_inputs": ["query", "method=rules"], "annotations_loaded_after_inference": True,
            "confirmation_executed": False, "end_to_end_answer_quality_evaluated": False,
            "model_quality_evaluated": False, "usage": {"model_calls": 0, "tokens": None,
            "token_status": "not_applicable_rules", "provider_cost": None, "provider_cost_status": "not_applicable_rules",
            "local_compute_cost": None, "local_compute_cost_status": "not_measured"},
            "limits": ["合成开发题，助手标注待人工复核；不是独立盲测或客户样本。",
                       "仅评测原文范围提案和 Unicode codepoint 跨度；不证明范围完整、事实正确或用户已确认。",
                       "有限规则没有执行 LLM 推理；费用不包含本机计算，未测量部分不能写成零。",
                       "需澄清题的候选集合没有唯一标注，故 pair 指标只覆盖明确题；全部题仍检验跨度、状态与确认门禁。",
                       "推理边界阻止通常的 Python 文件读取与网络调用，是回归保护，不是恶意代码沙箱。"],
            "summary": summary,
            "by_category": {name: summarize([item for item in results if item["category"] == name]) for name in manifest["categories"]},
            "by_grammar_scope": {name: summarize([item for item in results if item["grammar_scope"] == name])
                                 for name in ("supported", "outside_rules_v1")},
            "bad_cases": [item for item in results if item["bad_case"]], "cases": results}


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".data/validation/question-scope/report.json")
    parser.add_argument("--fail-on-bad-cases", action="store_true", help="质量坏例存在时退出 2；默认完成评测退出 0，并保留全部坏例。")
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    try:
        report = evaluate()
        write_report(args.output, report)
    except Exception:
        failure = {"schema_version": 1, "evaluation_completed": False, "quality_passed": None,
                   "error": "question_scope_evaluation_failed", "created_at": datetime.now(timezone.utc).isoformat()}
        try:
            write_report(args.output, failure)
        except OSError:
            pass
        print(json.dumps(failure, ensure_ascii=False))
        return 1
    print(json.dumps({"evaluation_completed": True, "quality_passed": report["quality_passed"],
                      "case_count": len(report["cases"]), "bad_case_count": report["summary"]["bad_case_count"]}, ensure_ascii=False))
    return 2 if args.fail_on_bad_cases and not report["quality_passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

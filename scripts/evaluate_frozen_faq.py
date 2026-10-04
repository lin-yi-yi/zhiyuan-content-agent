#!/usr/bin/env python3
"""Frozen synthetic FAQ evaluation; reserved is a declared role, never an unseen-data claim."""
import argparse
import ast
from contextlib import ExitStack
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import tempfile
from time import perf_counter
import unicodedata
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "scripts/fixtures/faq_frozen_v1"
FROZEN_MANIFEST_SHA256 = "27c1d05222777f3a8ed8690971589a910824d3bbb91adb42ca2cceef64e111c0"
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_industrial_faq import configure_offline, score_case


class EvaluationError(ValueError):
    """Fixed, sanitized operator-facing error; no source/provider exception text."""


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def normalized(value):
    return "".join(unicodedata.normalize("NFKC", value).split())


def require(condition, message):
    if not condition:
        raise EvaluationError(message)


def validate_contract(manifest, corpus, cases):
    """Structural/partition checks supplement byte hashes; neither proves semantic independence."""
    require(manifest["schema_version"] == 1 and manifest["dataset_id"] == corpus["dataset_id"] == cases["dataset_id"],
            "题集版本或标识不一致。")
    require(manifest["sample_origin"] == "synthetic" and manifest["annotation_author"] == "development_assistant"
            and manifest["annotation_review_status"] == "pending_human_review" and manifest["unseen_status"] == "not_claimed"
            and manifest["real_authorized_sample_count"] == 0, "合成来源、标签复核和非盲测声明不能省略或提升。")
    require(digest(canonical(manifest["configuration"])) == manifest["configuration_sha256"], "冻结配置哈希不匹配。")
    documents, questions = corpus["documents"], cases["cases"]
    require(isinstance(documents, list) and 0 < len(documents) <= 50 and isinstance(questions, list)
            and 0 < len(questions) <= 50, "题目或资料数量无效。")
    docs, families, products, texts, question_texts = {}, {}, {}, set(), set()
    for document in documents:
        key, family, split = document["key"], document["scenario_family"], document["split"]
        require(isinstance(key, str) and key and key not in docs and split in {"development", "reserved"}, "资料编号或分组无效。")
        require(family and families.setdefault(family, split) == split, "同一场景族不能跨 development/reserved。")
        require(document["state"] in {"verified", "legacy_verified_conflict"}, "未知资料状态。")
        content = document["content"]
        require(isinstance(content, str) and 40 <= len(content) <= 120000 and normalized(content) not in texts,
                "资料正文长度无效或资料重复。")
        texts.add(normalized(content))
        require(document["version_label"] and document["citations"], "资料缺少版本或结构化出处。")
        for citation in document["citations"]:
            require(all(isinstance(citation.get(field), str) and citation[field].strip() for field in
                        ("product_model", "parameter", "value", "claim", "excerpt", "locator", "source_url")), "结构化出处字段不完整。")
            product = normalized(citation["product_model"])
            require(products.setdefault(product, split) == split, "同一产品材料不能跨开发和保留组。")
            require(normalized(citation["value"]) in normalized(citation["excerpt"])
                    and normalized(citation["excerpt"]) in normalized(content), "资料标签不在原文摘录内。")
            require(citation["source_url"].startswith("https://example.com/frozen-faq-v1/"), "只允许固定合成出处。")
        docs[key] = document
    identifiers, counts, categories = set(), Counter(), defaultdict(Counter)
    for case in questions:
        identifier, family, split = case["id"], case["scenario_family"], case["split"]
        require(isinstance(identifier, str) and identifier and identifier not in identifiers, "题目 ID 重复或缺失。")
        identifiers.add(identifier)
        require(families.get(family) == split and split in {"development", "reserved"}, "问题不能跨场景族资料分组。")
        require(case["category"] in manifest["categories"], "题目分类无效。")
        text = normalized(case["question"])
        require(text and len(case["question"]) <= 1000 and text not in question_texts, "问题为空、过长或规范化后重复。")
        question_texts.add(text)
        counts[split] += 1
        categories[split][case["category"]] += 1
        require(type(case["should_refuse"]) is bool, "拒答标签必须是布尔值。")
        expected = case["expected_document_keys"]
        require(isinstance(expected, list) and len(set(expected)) == len(expected), "预期文档重复或无效。")
        require(all(key in docs and docs[key]["scenario_family"] == family for key in expected), "答案标签指向另一资料范围。")
        require(bool(expected) != case["should_refuse"], "可答/拒答标签与预期依据矛盾。")
        require(bool(case["required_fact_phrases"]) == bool(expected), "可答题必须有预设事实短语，拒答题不能借短语计通过。")
        require(all(isinstance(phrase, str) and phrase and any(phrase in docs[key]["content"] for key in expected)
                    for phrase in case["required_fact_phrases"]), "事实短语缺少对应资料原文。")
        required = case["required_facts"]
        require(isinstance(required, list) and len(required) <= 10, "参数契约数量无效。")
        for fact in required:
            require(set(fact) == {"product_model", "parameter"} and all(isinstance(value, str) and 0 < len(value.strip()) <= 200
                                                                       for value in fact.values()), "参数契约格式无效。")
        pairs = {(normalized(fact["product_model"]), normalized(fact["parameter"])) for fact in required}
        require(len(pairs) == len(required), "参数契约重复。")
        require(all(fact in required for fact in case["expected_missing_facts"]), "预期缺项不属于本题契约。")
        require(bool(case["expected_refusal_reasons"]) == case["should_refuse"], "拒答原因标签缺失或不适用。")
    require(dict(counts) == manifest["split_counts"] and
            dict(Counter(families.values())) == manifest["family_counts"], "冻结分组数量不一致。")
    require(all(set(group) == set(manifest["categories"]) for group in categories.values()), "每组必须覆盖全部预定类型。")


def load_frozen_dataset(directory=DATASET):
    try:
        directory = Path(directory)
        raw = (directory / "manifest.json").read_bytes()
        require(digest(raw) == FROZEN_MANIFEST_SHA256, "冻结清单哈希不匹配；修改题目、标签或配置须发布新版本。")
        manifest = json.loads(raw)
        payloads = {}
        for filename in ("corpus.json", "cases.json"):
            data = (directory / filename).read_bytes()
            require(digest(data) == manifest["files"][filename], "冻结题集文件哈希不匹配。")
            payloads[filename] = json.loads(data)
        validate_contract(manifest, payloads["corpus.json"], payloads["cases.json"])
        return manifest, payloads["corpus.json"]["documents"], payloads["cases.json"]["cases"]
    except EvaluationError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise EvaluationError("无法读取有效的冻结题集。") from None


def configure(folder, configuration, mode, cache_dir=None):
    configure_offline(folder)
    os.environ.update({"MODEL_PRICING_JSON": "[]", "RAG_RETRIEVAL_MODE": mode,
        "RAG_EMBEDDING_PROVIDER": configuration["embedding_provider"], "RAG_EMBEDDING_MODEL": configuration["embedding_model"],
        "RAG_EMBEDDING_BASE_URL": "", "RAG_EMBEDDING_API_KEY": "",
        "RAG_SEMANTIC_MIN_SCORE": str(configuration["semantic_min_score"]),
        "RAG_EMBEDDING_CACHE_DIR": str(cache_dir or Path(folder) / "unused-model-cache"),
        "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "HF_TOKEN": "", "HUGGING_FACE_HUB_TOKEN": "", "DO_NOT_TRACK": "1"})


def runtime_configuration(configuration, mode):
    from app.agent_core import embeddings, hybrid_retrieval, rag_service
    actual = embeddings.retrieval_config(mode)
    require(actual.provider == configuration["embedding_provider"] and actual.model == configuration["embedding_model"]
            and actual.semantic_threshold == configuration["semantic_min_score"], "运行时嵌入配置偏离冻结配置。")
    require((hybrid_retrieval.RRF_K, hybrid_retrieval.BM25_K1, hybrid_retrieval.BM25_B) ==
            (configuration["rrf_k"], configuration["bm25_k1"], configuration["bm25_b"]), "运行时融合参数偏离冻结配置。")
    # These are currently literal application settings. Fail closed if their
    # implementation changes instead of silently labelling a different config frozen.
    source = ast.parse(Path(rag_service.__file__).read_text())
    functions = {node.name: node for node in source.body if isinstance(node, ast.FunctionDef)}
    def keywords(function, call, keyword):
        return [item.value.value for node in ast.walk(functions[function]) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == call
                for item in node.keywords if item.arg == keyword and isinstance(item.value, ast.Constant)]
    require(keywords("answer_question", "search_knowledge", "min_score") == [configuration["candidate_min_score"]]
            and keywords("index_source", "split_document", "chunk_size") == [configuration["chunk_size"]]
            and keywords("index_source", "split_document", "chunk_overlap") == [configuration["chunk_overlap"]],
            "运行时候选或切块参数偏离冻结配置。")
    thresholds = [node.args[1].value for node in ast.walk(functions["select_evidence"]) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and len(node.args) == 2
                  and isinstance(node.args[0], ast.Constant) and node.args[0].value == "evidence_threshold"
                  and isinstance(node.args[1], ast.Constant)]
    require(thresholds == [configuration["lexical_min_overlap"]], "运行时词项门槛偏离冻结配置。")
    return {**configuration, "mode": mode, "index_fingerprint": actual.fingerprint if mode != "lexical" else "lexical-v2"}


def semantic_runtime(configuration, mode, cache_dir):
    if mode == "lexical":
        return {"kind": "lexical", "real_embedding_inference": False, "model_artifacts": None}
    require(cache_dir is not None and Path(cache_dir).is_dir(), "语义/混合模式必须显式指定已存在的 --cache-dir；未运行且不下载模型。")
    from app.agent_core import embeddings
    try:
        # FastEmbed respects HF_HUB_OFFLINE=1 and loads genuine cached ONNX files.
        # Use the same model factory and embed_texts path as application indexing.
        model = embeddings._fastembed_model(configuration["embedding_model"], str(cache_dir))
        actual_threads = getattr(model.model, "threads", None)
        require(actual_threads == configuration["embedding_threads"], "实际嵌入线程数与冻结配置不一致。")
        vectors = embeddings.embed_texts(["合成冻结评测的缓存推理检查"], embeddings.retrieval_config(mode), query=True)
        require(len(vectors[0]) == configuration["embedding_dimensions"], "实际向量维度与冻结配置不一致。")
        model_dir = Path(model.model._model_dir)
        files = sorted(path for path in model_dir.rglob("*") if path.is_file() and path.suffix in {".onnx", ".json", ".txt"})
        require(any(path.suffix == ".onnx" for path in files), "无法记录真实嵌入模型文件。")
        artifacts = [{"file": path.relative_to(model_dir).as_posix(), "sha256": file_digest(path)} for path in files]
        return {"kind": "real_fastembed_onnx", "real_embedding_inference": True, "dimensions": len(vectors[0]),
                "threads": actual_threads, "cache_access": "source_read_only_temporary_copy",
                "model_artifacts": artifacts, "fastembed_version": importlib.metadata.version("fastembed")}
    except EvaluationError:
        raise
    except Exception:
        raise EvaluationError("缓存模型未能离线加载/推理；本模式未完成，未使用模拟向量或降级。") from None


def copy_model_cache(source, target):
    """Give FastEmbed a disposable copy: even offline fallback may write cache/tmp."""
    source, target = Path(source).resolve(), Path(target)
    require(source.is_dir(), "必须显式选择现有模型缓存目录。")
    names = ("models--Qdrant--bge-small-zh-v1.5", "fast-bge-small-zh-v1.5")
    selected = [source / name for name in names if (source / name).is_dir()]
    require(bool(selected), "缓存模型不存在；该模式未运行，未使用模拟向量或降级。")
    for directory in selected:
        require(not directory.is_symlink(), "模型缓存目录不能指向所选缓存范围之外。")
        for path in directory.rglob("*"):
            # HF snapshots commonly link files to blobs within the chosen cache.
            require(not (path.is_symlink() and path.is_dir()) and path.resolve().is_relative_to(source),
                    "模型缓存含越界路径或目录链接。")
    target.mkdir(mode=0o700)
    for directory in selected:
        shutil.copytree(directory, target / directory.name, symlinks=False,
                        ignore=shutil.ignore_patterns(".locks", "*.lock", ".env", ".env.*"))
    return target


def file_digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def score_frozen_case(case, response, ids, elapsed_ms):
    result = score_case(case, response, ids, elapsed_ms)
    result["answer_validation"] = response.get("answer_validation")
    result["refusal_reason_matches"] = (response["refusal_reason"] in case["expected_refusal_reasons"]
                                         if case["should_refuse"] else None)
    expected = {(normalized(item["product_model"]), normalized(item["parameter"])) for item in case["expected_missing_facts"]}
    actual = {(normalized(item["product_model"]), normalized(item["parameter"])) for item in response.get("missing_facts", [])}
    result["missing_facts_match"] = expected == actual if case["required_facts"] else None
    result["bad_case_reasons"] = [key for key in ("false_accept", "false_refusal", "answer_missing_labelled_facts") if result[key]]
    if case["should_refuse"] and response["refused"] and not result["refusal_reason_matches"]:
        result["bad_case_reasons"].append("unexpected_refusal_reason")
    if result["missing_facts_match"] is False:
        result["bad_case_reasons"].append("missing_facts_mismatch")
    result["bad_case"] = bool(result["bad_case_reasons"])
    result["cost"]["reason"] = "本地摘录，无生成模型请求；语义模式仅用本地真实嵌入，设备与人工成本未测量。"
    return result


def summarize(cases):
    def metric(values):
        values = [value for value in values if value is not None]
        return {"numerator": round(sum(values), 6), "denominator": len(values),
                "value": round(sum(values) / len(values), 6) if values else None}
    return {"sample_count": len(cases), "bad_case_count": sum(case["bad_case"] for case in cases),
            "false_accept_rate": metric([case["false_accept"] for case in cases if case["should_refuse"]]),
            "false_refusal_rate": metric([case["false_refusal"] for case in cases if not case["should_refuse"]]),
            "candidate_document_recall_at_k": metric([case["candidate_document_recall_at_k"] for case in cases]),
            "adopted_document_recall": metric([case["adopted_document_recall"] for case in cases]),
            "labelled_phrase_support_rate": metric([case["labelled_fact_support"] for case in cases if not case["should_refuse"]]),
            "mean_latency_ms": metric([case["latency_ms"] for case in cases])}


def evaluate_cases(client, documents, cases, configuration, mode):
    from app.db.session import SessionLocal
    from app.models.evidence_note import EvidenceNote
    ids, scopes, injections = {}, {}, []
    def call(method, path, body, status=200):
        response = client.request(method, path, json=body)
        require(response.status_code == status, "临时合成资料导入、核验或问答接口未按约定完成。")
        return response.json()
    for document in documents:
        family = document["scenario_family"]
        if family not in scopes:
            scopes[family] = call("POST", "/api/v04/knowledge-bases", {"name": f"冻结合成 {family}"}, 201)["id"]
        scope = {"knowledge_base_id": scopes[family]}
        note = call("POST", "/api/evidence/notes", {**scope,
            **{key: document[key] for key in ("title", "content", "version_label", "citations")},
            "source_url": document["citations"][0]["source_url"], "provider": "manual", "rights_basis": "own",
            "rights_note": "助手编写的合成材料；脚本审核不是人工标签审定。"}, 201)
        ids[document["key"]] = {"note_id": note["id"], "document_id": None}
        path = f"/api/evidence/notes/{note['id']}"
        if document["state"] == "legacy_verified_conflict":
            call("POST", path + "/review", {**scope, "decision": "verify", "confirmed_sources": True}, 409)
            with SessionLocal() as db:
                row = db.get(EvidenceNote, note["id"])
                row.review_status, row.verified_at = "verified", datetime.now(timezone.utc)
                db.commit()
            injections.append({"document_key": document["key"], "kind": "temporary_legacy_verified_conflict",
                               "normal_verification_was_blocked": True, "index_state": "not_indexed"})
        else:
            call("POST", path + "/review", {**scope, "decision": "verify", "confirmed_sources": True,
                 "note": "脚本模拟核验合成资料，标签尚待人工复核。"})
            ids[document["key"]]["document_id"] = call("POST", path + "/index", scope)["document_id"]
    results = []
    for case in cases:
        start = perf_counter()
        response = call("POST", "/api/v04/rag/answer", {"query": case["question"], "provider": "local",
            "knowledge_base_id": scopes[case["scenario_family"]], "top_k": configuration["top_k"],
            "retrieval_mode": mode, "required_facts": case["required_facts"]})
        results.append(score_frozen_case(case, response, ids, (perf_counter() - start) * 1000))
    return results, injections


def code_identity():
    def git(*args):
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None
    paths = ["scripts/evaluate_frozen_faq.py", "backend/app/agent_core/rag_service.py",
             "backend/app/agent_core/embeddings.py", "backend/app/agent_core/hybrid_retrieval.py",
             "backend/app/agent_core/evidence_policy.py", "backend/app/agent_core/fact_answer.py",
             "backend/app/models/evidence_note.py"]
    status = git("status", "--porcelain")
    return {"git_commit": git("rev-parse", "HEAD"), "worktree_dirty": None if status is None else bool(status),
            "implementation_sha256": {path: file_digest(ROOT / path) for path in paths}}


def write_report(path, report):
    path = Path(path)
    require(not path.exists(), "报告已存在，拒绝覆盖；请使用新的输出文件。")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".frozen-faq-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("development", "reserved"), default="development")
    parser.add_argument("--mode", choices=("lexical", "semantic", "hybrid"), default="lexical")
    parser.add_argument("--dataset-dir", type=Path, default=DATASET, help="仅接受登记哈希一致的冻结包或其副本")
    parser.add_argument("--cache-dir", type=Path, help="语义/混合显式选择已有 FastEmbed 缓存，不下载")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    stamp = datetime.now(timezone.utc)
    output = args.output or ROOT / f".data/evaluations/frozen-faq-{args.split}-{args.mode}-{stamp:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.json"
    if output.exists() or output.is_symlink():
        parser.exit(2, "报告已存在，拒绝覆盖；请指定新文件。\n")
    if output.resolve().is_relative_to(args.dataset_dir.resolve()):
        parser.exit(2, "报告不能写入冻结题集目录。\n")
    report = {"schema_version": 1, "checked_at": stamp.isoformat(), "completed": False,
              "split": args.split, "mode": args.mode, "cases": [], "unseen_status": "not_claimed",
              "external_generation_requests": 0, "generation_quality_evaluated": False}
    outbound = []
    try:
        manifest, documents, cases = load_frozen_dataset(args.dataset_dir)
        selected = [case for case in cases if case["split"] == args.split]
        selected_documents = [doc for doc in documents if doc["split"] == args.split]
        config = manifest["configuration"]
        report.update({"dataset_id": manifest["dataset_id"], "manifest_sha256": FROZEN_MANIFEST_SHA256,
            "file_sha256": manifest["files"], "frozen_at": manifest["frozen_at"],
            "configuration": config, "configuration_sha256": manifest["configuration_sha256"],
            "sample_origin": manifest["sample_origin"], "annotation_author": manifest["annotation_author"],
            "annotation_review_status": manifest["annotation_review_status"], "real_authorized_sample_count": 0,
            "reserved_policy": manifest["reserved_policy"], "tuning_exclusion_independently_verified": False,
            "legacy_development_datasets": manifest["legacy_development_datasets"], "limitations": manifest["limitations"],
            "code": code_identity()})
        if args.mode != "lexical":
            require(args.cache_dir is not None and args.cache_dir.is_dir(), "必须显式提供已存在的 --cache-dir；该模式未运行。")
        with TemporaryDirectory(prefix="frozen-faq-") as folder, ExitStack() as stack:
            cache = copy_model_cache(args.cache_dir, Path(folder) / "model-cache") if args.mode != "lexical" else None
            configure(folder, config, args.mode, cache)
            sys.path.insert(0, str(ROOT / "backend"))
            def deny_outbound(*unused, **unused_kwargs):
                outbound.append("blocked")
                raise EvaluationError("评测禁止出站连接。")
            stack.enter_context(patch.object(socket.socket, "connect", deny_outbound))
            stack.enter_context(patch.object(socket.socket, "connect_ex", deny_outbound))
            from fastapi.testclient import TestClient
            from app.main import app
            from app.db.session import engine
            try:
                report["runtime_configuration"] = runtime_configuration(config, args.mode)
                report["runtime_configuration_sha256"] = digest(canonical(report["runtime_configuration"]))
                report["embedding"] = semantic_runtime(config, args.mode, cache)
                with TestClient(app) as client:
                    report["cases"], report["fault_injections"] = evaluate_cases(client, selected_documents, selected, config, args.mode)
                require(not outbound, "评测检测到出站连接尝试，本轮结果不作为完成验收。")
            finally:
                from app.agent_core.vector_store import close_vector_stores
                close_vector_stores()
                engine.dispose()
        report.update({"completed": True, "summary": summarize(report["cases"]),
            "by_category": {category: summarize([item for item in report["cases"] if item["category"] == category])
                            for category in manifest["categories"]},
            "bad_case_ids": [item["id"] for item in report["cases"] if item["bad_case"]],
            "quality_gate": "observe_all_cases_no_score_target_no_automatic_tuning",
            "metric_definitions": {"candidate_document_recall_at_k": "预设可答文档在候选中的覆盖；均值分母为有文档标签的题数。",
                "labelled_phrase_support_rate": "预设短语同时出现在回答和所引摘录中的比例；不检查语义蕴含、否定理解或额外断言。",
                "false_accept_rate": "预设应拒答却接纳的题数 / 预设应拒答题数。",
                "false_refusal_rate": "预设可答却拒绝的题数 / 预设可答题数。",
                "bad_case_count": "包括错误接纳、误拒、缺预设事实短语、拒答原因或缺项列表不符；全部保留。"},
            "cost": {"external_generation_requests": 0, "provider_invoice_amount": None, "model_tokens": None,
                     "estimated_model_cost": None, "human_work_minutes": None, "device_cost": None}})
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc) if isinstance(exc, EvaluationError)
                           else "评测未完成；未采用旧报告、模拟向量或降级结果。"}
    report["outgoing_connection_attempts"] = len(outbound)
    try:
        write_report(output, report)
    except (OSError, EvaluationError):
        parser.exit(2, "评测报告未保存：目标已存在或目录不可写。\n")
    print(json.dumps({"output": str(output), "completed": report["completed"], "split": args.split, "mode": args.mode,
                      "summary": report.get("summary"), "bad_case_ids": report.get("bad_case_ids", []),
                      "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

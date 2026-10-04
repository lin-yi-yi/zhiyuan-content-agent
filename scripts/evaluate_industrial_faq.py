"""Reproduce an industrial FAQ workflow in a disposable, offline application.

Run: .venv/bin/python scripts/evaluate_industrial_faq.py [--serve --port 8772]
Uses the real FastAPI app, SQL/index/retrieval, graph and review/delivery routes.
All material and review actions are synthetic, including the scripted reviewer.
Local mode is deterministic excerpt/rule code, not an LLM quality evaluation.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "scripts/fixtures/industrial_faq_synthetic.json"


def configure_offline(folder):
    """Must execute before any application import; never read workspace .env."""
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1", "SAAS_MODE": "false", "APP_ENV": "local",
        "DATABASE_URL": f"sqlite:///{Path(folder) / 'industrial-faq.db'}",
        "RAG_RETRIEVAL_MODE": "lexical", "RAG_VECTOR_PATH": str(Path(folder) / "vectors"),
        "AIHOT_ENABLED": "false", "GITHUB_ENABLED": "false",
        "DIAGNOSTIC_LOG_DIR": "",
    })
    for provider in ("DEEPSEEK", "QWEN", "DOUBAO", "KIMI", "OPENAI"):
        os.environ[f"{provider}_API_KEY"] = ""
    for namespace in ("LANGSMITH", "LANGCHAIN"):
        os.environ[f"{namespace}_TRACING"] = "false"
        os.environ[f"{namespace}_TRACING_V2"] = "false"
        os.environ[f"{namespace}_API_KEY"] = ""
    os.environ.pop("LANGCHAIN_HANDLER", None)
    for task in ("DEFAULT_LLM", "TOPIC_SCORE", "DRAFT_GENERATION", "CARD_GENERATION", "COMPLIANCE_CHECK"):
        os.environ[f"{task}_PROVIDER"] = "local"
        os.environ[f"{task}_MODEL"] = "local-rule-based-v0"


def score_case(case, response, ids, elapsed_ms):
    """Exact pre-labelled phrase checks; deliberately not a semantic judge."""
    candidates, citations = response["retrieval_candidates"], response["citations"]
    expected = {ids[key]["document_id"] for key in case["expected_document_keys"]}
    retrieved = {hit["document_id"] for hit in candidates}
    adopted = {hit["document_id"] for hit in citations}
    text = response["answer"]
    cited_text = "\n".join(hit.get("content", "") for hit in citations)
    phrases = case["required_fact_phrases"]
    phrase_checks = [{"phrase": phrase, "in_answer": phrase in text, "in_cited_excerpt": phrase in cited_text}
                     for phrase in phrases]
    support = all(item["in_answer"] and item["in_cited_excerpt"] for item in phrase_checks) if phrases else None
    wrong_accept = case["should_refuse"] and not response["refused"]
    false_refusal = not case["should_refuse"] and response["refused"]
    missed_facts = not case["should_refuse"] and not response["refused"] and not support
    return {
        **case, "refused": response["refused"], "refusal_reason": response["refusal_reason"],
        "candidate_document_recall_at_k": len(expected & retrieved) / len(expected) if expected else None,
        "adopted_document_recall": len(expected & adopted) / len(expected) if expected else None,
        "candidate_relevant_fraction": sum(hit["document_id"] in expected for hit in candidates) / len(candidates)
            if expected and candidates else (0.0 if expected else None),
        "false_accept": wrong_accept, "false_refusal": false_refusal,
        "answer_missing_labelled_facts": bool(missed_facts), "labelled_fact_support": support,
        "phrase_checks": phrase_checks, "latency_ms": round(elapsed_ms, 2),
        "bad_case": bool(wrong_accept or false_refusal or missed_facts),
        "answer": text, "citation_check": response["citation_check"],
        "answerability": response.get("answerability"), "missing_facts": response.get("missing_facts", []),
        "retrieval_candidates": candidates, "citations": citations,
        "evidence_health": response["evidence_health"],
        "cost": {"external_model_requests": 0, "model_tokens": None, "provider_invoice_amount": None,
                 "estimated_model_cost": None, "reason": "本地词项检索和规则摘录，无模型请求；token/账单不适用，人工及设备成本未测量。"},
    }


def permission_checks(folder):
    """Reuse authenticated organization/role contracts in a separate subprocess."""
    env = os.environ.copy()
    env.update({"DATABASE_URL": "sqlite:///:memory:", "PYTHONPATH": str(ROOT / "backend"),
                "RAG_VECTOR_PATH": str(Path(folder) / "permission-vectors")})
    command = [sys.executable, "-m", "pytest", "tests/test_delivery_saas.py", "tests/test_pilot_saas.py",
               "tests/test_saas_isolation.py::test_roles_and_revocation_apply_to_legacy_and_evidence_routes", "-q"]
    started = perf_counter()
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    return {"passed": result.returncode == 0, "command": " ".join(command),
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
            "output": result.stdout.strip(), "stderr": result.stderr.strip(),
            "boundary": "隔离临时 SaaS 登录会话与组织数据库；不是本地 workspace_id 充当用户鉴权，也不是客户环境测试。"}


def run_evaluation(client, dataset, output):
    from app.db.session import SessionLocal
    from app.models.evidence_note import EvidenceNote

    started = perf_counter()
    checks, trace, ids, kbs = {}, [], {}, {}
    now = datetime.now(timezone.utc)

    def call(method, path, body=None, expected=200, params=None):
        before = perf_counter()
        response = client.request(method, path, json=body, params=params)
        trace.append({"method": method, "path": path, "status": response.status_code,
                      "latency_ms": round((perf_counter() - before) * 1000, 2)})
        if response.status_code != expected:
            raise RuntimeError(f"{method} {path}: expected {expected}, got {response.status_code}: {response.text}")
        return response.json()

    def get_run(run_id):
        return call("GET", f"/api/agent-runs/{run_id}")

    def new_run(goal, group="main", brand_id=None, required_facts=None):
        created = call("POST", "/api/agent-runs", {"goal": goal, "provider": "local", "auto_score": False,
            "use_rag": True, "workflow_key": "product_faq", "knowledge_base_id": kbs[group],
            "brand_profile_id": brand_id, "required_facts": required_facts or []}, 201)
        return get_run(created["id"])

    checks["starts_without_user_documents"] = call("GET", "/api/knowledge/documents")["total"] == 0
    for group in ("main", "expired", "empty"):
        kb = call("POST", "/api/v04/knowledge-bases", {"name": f"工业 FAQ 合成 {group}",
            "purpose": "可丢弃固定开发样本，无真实客户资料"}, 201)
        kbs[group] = kb["id"]
    brand = call("POST", "/api/brands", {"name": "合成星桥工业设备（虚构）", "knowledge_base_id": kbs["main"],
        "audience": "演示中的工业产品售前人员", "tone": "克制，逐项标注参数来源", "data_policy": "local_only",
        "prohibited_claims": "绝对可靠\n保证节省", "call_to_action": "由资料负责人确认缺失参数"}, 201)

    for document in dataset["documents"]:
        scope = {"knowledge_base_id": kbs[document["group"]]}
        url = f"https://example.com/synthetic-industrial/{document['key']}"
        note = call("POST", "/api/evidence/notes", {**scope,
            **{key: document[key] for key in ("title", "content", "version_label")},
            "source_url": url, "provider": "manual", "rights_basis": "own",
            "rights_note": "开发者自编合成样本，不是客户授权声明",
            "expires_at": (now + timedelta(days=30)).isoformat(),
            "citations": [{**citation, "source_url": url + "#paragraph-1"} for citation in document["citations"]]}, 201)
        path = f"/api/evidence/notes/{note['id']}"
        call("POST", path + "/index", scope, 409)
        if document.get("state") == "conflicting_pending":
            rejected = call("POST", path + "/review", {**scope, "decision": "verify", "confirmed_sources": True}, 409)
            checks["conflicting_specification_cannot_be_verified"] = "冲突" in rejected["detail"]
            ids[document["key"]] = {"note_id": note["id"], "document_id": None, "review_error": rejected}
            continue
        call("POST", path + "/review", {**scope, "decision": "verify", "confirmed_sources": True,
            "note": "脚本模拟审核开发者预先标注的合成材料；不是人工已完成真实客户核验"})
        indexed = call("POST", path + "/index", scope)
        repeated = call("POST", path + "/index", scope)
        checks[f"index_idempotent_{document['key']}"] = repeated["deduplicated"] and indexed["document_id"] == repeated["document_id"]
        ids[document["key"]] = {"note_id": note["id"], "document_id": indexed["document_id"]}

    # Explicit time-fault fixture only: normal imports/generation/reviews use HTTP.
    # The immutable source API intentionally offers no "edit verified expiry" route.
    with SessionLocal() as db:
        db.get(EvidenceNote, ids["expired"]["note_id"]).expires_at = now - timedelta(days=1)
        db.commit()
    call("POST", f"/api/evidence/notes/{ids['expired']['note_id']}/index", {"knowledge_base_id": kbs["expired"]}, 409)
    checks["expired_reindex_blocked"] = True
    call("GET", f"/api/evidence/notes/{ids['spec']['note_id']}", expected=404, params={"knowledge_base_id": kbs["empty"]})
    checks["wrong_knowledge_scope_is_404"] = True

    cases = []
    for case in dataset["cases"]:
        before = perf_counter()
        response = call("POST", "/api/v04/rag/answer", {"query": case["question"], "provider": "local", "top_k": 3,
            "knowledge_base_id": kbs[case["group"]]})
        cases.append(score_case(case, response, ids, (perf_counter() - before) * 1000))
    checks["expired_note_excluded_from_retrieval"] = next(item for item in cases if item["id"] == "expired_material")["refused"]
    checks["empty_scope_does_not_leak_other_brand_documents"] = next(item for item in cases if item["id"] == "empty_scope")["refused"]
    checks["free_questions_do_not_claim_answerability_assessed"] = all(item["answerability"] == "not_assessed" for item in cases)

    structured_cases = []
    for case in dataset["structured_contract_cases"]:
        before = perf_counter()
        response = call("POST", "/api/v04/rag/answer", {"query": case["question"], "provider": "local", "top_k": 3,
            "knowledge_base_id": kbs[case["group"]], "required_facts": case["required_facts"]})
        structured_cases.append(score_case(case, response, ids, (perf_counter() - before) * 1000))
    checks["explicit_requirements_gate_missing_and_partial_facts"] = all(
        not item["bad_case"] and (not item["should_refuse"] or
        (item["refusal_reason"] == "missing_structured_facts" and item["missing_facts"])) for item in structured_cases)

    missing = new_run("合成星桥 XP-24 额定电压", "empty")
    checks["empty_evidence_stops_before_draft"] = missing["status"] == "failed" and missing["draft_id"] is None
    call("GET", f"/api/agent-runs/{missing['id']}/delivery", expected=409)

    requirements = [{"product_model": "合成星桥 XP-24", "parameter": value} for value in ("额定电压", "额定流量")]
    run = new_run("合成星桥 XP-24 额定电压与额定流量答疑", brand_id=brand["id"], required_facts=requirements)
    run_id, draft_id = run["id"], run["draft_id"]
    checks["generated_via_real_graph_to_review"] = run["status"] == "awaiting_review" and bool(draft_id)
    checks["draft_has_real_citations"] = bool(run["result_json"].get("citations")) and "[chunk:" in run["draft"]["body_text"]
    missing_contract = new_run("合成星桥 XP-24 售价为多少人民币？", brand_id=brand["id"], required_facts=[
        {"product_model": "合成星桥 XP-24", "parameter": "售价"}])
    checks["missing_parameter_stops_before_generation"] = missing_contract["status"] == "failed" and missing_contract["draft_id"] is None
    call("GET", f"/api/agent-runs/{missing_contract['id']}/delivery", expected=409)
    route = f"/api/agent-runs/{run_id}"
    call("GET", route + "/delivery", expected=409)
    call("POST", route + "/review", {"decision": "reject", "note": "合成审核：请将答疑标题和适用范围写清楚"})
    call("PUT", f"/api/drafts/{draft_id}", {"selected_title": "合成星桥 XP-24 参数答疑（模拟返工）"})
    call("POST", route + "/submit-review")
    call("POST", route + "/review", {"decision": "approve", "note": "脚本模拟重新核对本合成版本，不是客户批准"})
    delivery1 = call("GET", route + "/delivery")
    call("PUT", f"/api/drafts/{draft_id}", {"selected_title": "合成星桥 XP-24 参数答疑（已重新审核版本）"})
    invalidated = get_run(run_id)
    call("GET", route + "/delivery", expected=409)
    checks["edit_invalidates_previous_approval"] = invalidated["status"] == "awaiting_review" and "review" not in invalidated["result_json"]
    approved = call("POST", route + "/review", {"decision": "approve", "note": "脚本模拟第2版重审；未向真实客户交付"})
    delivery2 = call("GET", route + "/delivery")
    checks["reapproval_changes_delivery_hash"] = delivery1["review"]["content_hash"] != delivery2["review"]["content_hash"]
    checks["export_matches_approved_revision"] = delivery2["body_text"] == approved["draft"]["body_text"] and delivery2["title"] == approved["draft"]["selected_title"]
    checks["export_retains_version_and_locator"] = bool(delivery2["citations"]) and all(
        item.get("version_label") and item.get("chunk_id") and item.get("locators") for item in delivery2["citations"])
    export_path = output.with_suffix(".delivery.md")
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(delivery2["markdown"], encoding="utf-8")

    async def injected_failure(*args, **kwargs):
        raise RuntimeError("synthetic one-time generation failure")

    # Fault injection replaces one node call, never seeds successful task records.
    with patch("app.services.content_growth_agent.generate_evidence_draft", side_effect=injected_failure):
        failed = new_run("合成星桥 XP-24 额定流量", brand_id=brand["id"], required_facts=requirements)
    checks["injected_failure_has_no_partial_draft"] = failed["status"] == "failed" and failed["draft_id"] is None
    call("POST", f"/api/agent-runs/{failed['id']}/retry")
    recovered = get_run(failed["id"])
    checks["retry_recovers_to_review"] = recovered["status"] == "awaiting_review" and bool(recovered["draft_id"])
    call("POST", f"/api/agent-runs/{failed['id']}/retry", expected=409)
    checks["repeated_retry_blocked"] = True

    # Preserve the primary run as valid for browser inspection. Use the recovered
    # run to demonstrate that material expiry also blocks an approved export.
    call("POST", f"/api/agent-runs/{failed['id']}/review", {"decision": "approve", "note": "合成撤销门禁演示"})
    with SessionLocal() as db:
        note = db.get(EvidenceNote, ids["spec"]["note_id"])
        original_expiry = note.expires_at
        note.expires_at = now - timedelta(days=1)
        db.commit()
    call("GET", f"/api/agent-runs/{failed['id']}/delivery", expected=409)
    checks["expired_material_blocks_previously_approved_export"] = True
    with SessionLocal() as db:
        db.get(EvidenceNote, ids["spec"]["note_id"]).expires_at = original_expiry
        db.commit()

    counts = {"samples": len(cases), "answerable": sum(not item["should_refuse"] for item in cases),
              "should_refuse": sum(item["should_refuse"] for item in cases)}
    counts.update({name: sum(item[name] for item in cases) for name in
                   ("false_accept", "false_refusal", "answer_missing_labelled_facts", "bad_case")})
    return {
        "checked_at": now.isoformat(), "dataset_id": dataset["dataset_id"], "dataset_label": dataset["label"],
        "dataset_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "sample_origin": "synthetic", "real_authorized_sample_count": 0,
        "annotation_review_status": dataset["annotation_review_status"], "dataset_provenance": dataset["provenance"],
        "annotation_policy": dataset["annotation_policy"], "counts": counts,
        "retrieval": {"mode": "lexical", "top_k": 3, "thresholds": "repository defaults, not tuned to this fixture"},
        "fact_support_method": "逐字检查预标注事实短语同时存在于回答与被引摘录；不是语义蕴含、事实真伪或完整回答准确率。",
        "generation": {"provider": "local", "kind": "rule_based_and_extractive", "is_llm": False},
        "checks": checks, "cases": cases, "bad_case_ids": [item["id"] for item in cases if item["bad_case"]],
        "structured_contract_cases": structured_cases,
        "structured_contract_counts": {"samples": len(structured_cases), "bad_case": sum(item["bad_case"] for item in structured_cases),
            "false_accept": sum(item["false_accept"] for item in structured_cases),
            "false_refusal": sum(item["false_refusal"] for item in structured_cases)},
        "permission_checks": None, "http_trace": trace, "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "document_ids": ids, "knowledge_base_ids": kbs,
        "workflow": {"primary_run_id": run_id, "draft_id": draft_id, "failed_then_retried_run_id": failed["id"],
                     "missing_evidence_run_id": missing["id"], "missing_parameter_run_id": missing_contract["id"], "approved_snapshot": approved,
                     "delivery": delivery2, "export_path": str(export_path)},
        "cost": {"external_model_requests": 0, "provider_invoice_amount": None, "estimated_model_cost": None,
                 "model_tokens": None, "human_work_minutes": None, "support_minutes": None,
                 "reason": "无在线调用；不存在供应商账单测量，人工审核是脚本模拟，耗时不等于客户工时或ROI。"},
        "fault_injections": ["只在临时 DB 更改 expires_at 模拟时间已到期，然后恢复主资料有效期供浏览器检查。",
                             "单次 generate_evidence_draft 异常注入，随后恢复真实函数并通过 HTTP retry 恢复。"],
        "limitations": ["固定合成开发集，作者同时指定材料与标签；不是独立测试集。真实授权样本为0。",
                        "本地 lexical 不验证中文向量模型、BM25/RRF 混合检索或在线模型质量。",
                        "检索到同型号材料不表示覆盖具体问题；缺价或响应时间的误接收完整保留为坏例。",
                        "冲突核验门禁只覆盖显式结构化参数；未结构化自由文本冲突仍需人工检查。",
                        "required_facts 由调用者明确填写并严格匹配，不推断自由问题的型号别名、参数同义词或全部语义覆盖。",
                        "自动化审核只验证状态门禁；未证明真实人审正确、真实联调、客户验收或付款。"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / ".data/evaluations/industrial-faq-evaluation.json",
                        help="报告路径；默认写入 .data/evaluations，不覆盖仓库中的历史验收")
    parser.add_argument("--serve", action="store_true", help="保留临时合成环境供浏览器查看；退出清除数据库")
    parser.add_argument("--port", type=int, default=8772)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024–65535")
    dataset = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with TemporaryDirectory(prefix="industrial-faq-eval-") as folder, ExitStack() as stack:
        configure_offline(folder)
        sys.path.insert(0, str(ROOT / "backend"))
        from fastapi.testclient import TestClient
        from app.main import app
        from app.db.session import engine
        from app.llm.router import router
        assert all(not cfg["api_key"] for name, cfg in router.PROVIDERS.items() if name != "local")
        outbound_attempts = []

        def deny_outbound(*args, **kwargs):
            outbound_attempts.append("blocked")
            raise RuntimeError("Offline evaluation prohibits outgoing socket connections")

        stack.enter_context(patch.object(socket.socket, "connect", deny_outbound))
        stack.enter_context(patch.object(socket.socket, "connect_ex", deny_outbound))
        try:
            with TestClient(app) as client:
                report = run_evaluation(client, dataset, args.output)
            report["permission_checks"] = permission_checks(folder)
            report["checks"]["authenticated_permissions"] = report["permission_checks"]["passed"]
            report["checks"]["no_outgoing_connections_attempted"] = not outbound_attempts
            report["workflow_checks_passed"] = all(report["checks"].values())
            report["quality_gate"] = "observe_and_keep_all_bad_cases; no production accuracy claim"
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"workflow_checks_passed": report["workflow_checks_passed"], "counts": report["counts"],
                "bad_case_ids": report["bad_case_ids"], "checks": report["checks"], "output": str(args.output),
                "browser_run_id": report["workflow"]["primary_run_id"]}, ensure_ascii=False, indent=2), flush=True)
            if not report["workflow_checks_passed"]:
                raise SystemExit(1)
            if args.serve:
                import uvicorn
                print(f"仅合成数据：http://127.0.0.1:{args.port}/ （Ctrl+C 后清除临时DB；报告与导出文件保留）", flush=True)
                uvicorn.run(app, host="127.0.0.1", port=args.port)
        except Exception as exc:
            failure_path = args.output.with_suffix(".failed.json")
            failure_path.parent.mkdir(parents=True, exist_ok=True)
            failure_path.write_text(json.dumps({
                "checked_at": datetime.now(timezone.utc).isoformat(), "completed": False,
                "error_type": type(exc).__name__, "previous_report_is_not_this_attempt": True,
                "message": "本轮未完成，之前成功报告不代表本轮结果；查看本次命令错误并重新运行。",
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()

"""Exercise the live note→review→index→retrieval→revoke flow with synthetic material.

Creates a separate, clearly labelled knowledge base; retains it for inspection.
No real news is copied, no generation model is called, no thresholds are tuned.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx


DOCUMENTS = [
    {"key": "aurora", "title": "合成案例：极光项目的信源核验", "content":
     "极光项目的信源核验规范（完全虚构的开发样本）：维护者发布记录可以作为版本变化的一手出处，媒体转载只用于发现线索。"
     "每条结论必须保存原文摘录、来源链接和版本号。人工核对后单独确认入库，不能用推荐分替代真实性核验。",
     "claim": "推荐分不能替代真实性核验", "excerpt": "人工核对后单独确认入库，不能用推荐分替代真实性核验。"},
    {"key": "lighthouse", "title": "合成案例：灯塔项目的资料时效", "content":
     "灯塔项目的资料时效规范（完全虚构的开发样本）：资料入库前记录适用版本和到期时间。资料到期后停止作为检索证据，页面提示重新核验。"
     "修订正文需要创建新版本笔记，并撤销旧版本的核验记录。撤销核验会立即停用关联文档，但保留记录以便审阅。",
     "claim": "到期资料停止参与检索", "excerpt": "资料到期后停止作为检索证据，页面提示重新核验。"},
    {"key": "cedar", "title": "合成案例：雪松项目的接口冷却", "content":
     "雪松项目的接口冷却规范（完全虚构的开发样本）：接口返回限流状态时，读取重试时间并暂停刷新。缓存只能暂时减少重复请求，不能永久保留撤销内容。"
     "上游明确撤销访问后清空相关缓存，并在界面展示错误。更换查询条件不能绕过提供方统一冷却时间。",
     "claim": "更换查询不能绕过统一冷却", "excerpt": "更换查询条件不能绕过提供方统一冷却时间。"},
]
CASES = [
    ("极光项目怎样判断推荐分的作用？", "aurora"),
    ("极光项目每条结论需要保存什么？", "aurora"),
    ("灯塔项目资料到期后怎么处理？", "lighthouse"),
    ("灯塔项目如何修订正文和撤销旧版本？", "lighthouse"),
    ("雪松项目接口限流时怎么处理？", "cedar"),
    ("雪松项目更换查询可以绕过冷却吗？", "cedar"),
    ("土星环的主要成分是什么？", None),
    ("意大利面需要煮几分钟？", None),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=Path("docs/validation/evidence-evaluation.json"))
    args = parser.parse_args()
    if urlsplit(args.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This acceptance script only writes to a local application.")
    now = datetime.now(timezone.utc)
    checks = {}
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=180, trust_env=False) as client:
        def post(path, body, status=200):
            response = client.post(path, json=body)
            if response.status_code != status:
                raise RuntimeError(f"{path}: expected {status}, got {response.status_code}")
            return response.json()
        kb = post("/api/v04/knowledge-bases", {"name": f"信源闭环验收 {now:%Y%m%d-%H%M%S}",
            "purpose": "独立的合成开发样本，不含真实资讯、不代表生产准确率"}, 201)
        scope = {"knowledge_base_id": kb["id"]}
        ids, note_ids = {}, {}
        for document in DOCUMENTS:
            url = f"https://example.com/synthetic/{document['key']}"
            note = post("/api/evidence/notes", {**scope, "title": document["title"], "content": document["content"],
                "source_url": url, "provider": "manual", "version_label": "synthetic-v1",
                "rights_basis": "own", "expires_at": (now + timedelta(days=30)).isoformat(),
                "citations": [{"claim": document["claim"], "excerpt": document["excerpt"],
                               "source_url": url + "#policy", "locator": "人工编写的合成规范"}]}, 201)
            path = f"/api/evidence/notes/{note['id']}"
            post(path + "/index", scope, 409)
            post(path + "/review", {**scope, "decision": "verify", "confirmed_sources": False}, 409)
            post(path + "/review", {**scope, "decision": "verify", "confirmed_sources": True,
                "note": "验收脚本核对本人编写的合成样本；不是外部事实核验"})
            indexed = post(path + "/index", scope)
            repeated = post(path + "/index", scope)
            assert repeated["deduplicated"] and repeated["document_id"] == indexed["document_id"]
            ids[document["key"]], note_ids[document["key"]] = indexed["document_id"], note["id"]
        checks["pending_and_unconfirmed_blocked"] = True
        checks["index_idempotent"] = True
        evaluation = post("/api/knowledge/evaluate", {**scope, "top_k": 3,
            "dataset_label": "信源治理合成开发集：3文档8题；非独立测试集、不调阈值、非生产效果",
            "cases": [{"question": question, "should_refuse": key is None,
                       "expected_document_ids": [ids[key]] if key else []} for question, key in CASES]})
        adopted = [citation for case in evaluation["cases"] for citation in case["citations"]]
        checks["adopted_citations_have_live_note_and_claims"] = bool(adopted) and all(
            citation["metadata"]["evidence"]["note_id"] in note_ids.values()
            and citation["metadata"]["evidence"]["citations"] for citation in adopted)
        revoked_id = note_ids["lighthouse"]
        post(f"/api/evidence/notes/{revoked_id}/review", {**scope, "decision": "revoke", "note": "演示撤销后排除"})
        after = post("/api/v04/rag/answer", {**scope, "query": CASES[2][0], "provider": "local"})
        checks["revoked_document_excluded"] = all(c["document_id"] != ids["lighthouse"]
            for c in after["retrieval_candidates"] + after["citations"])
        checks["revocation_warning_visible"] = after["evidence_health"]["unverified_count"] == 1
        post(f"/api/evidence/notes/{revoked_id}/index", scope, 409)
        note = post("/api/evidence/notes", {**scope, "title": "合成：已过期样本", "content": DOCUMENTS[0]["content"],
            "source_url": "https://example.com/synthetic/expired", "rights_basis": "own",
            "expires_at": (now - timedelta(days=1)).isoformat()}, 201)
        post(f"/api/evidence/notes/{note['id']}/review", {**scope, "decision": "verify", "confirmed_sources": True}, 409)
        checks["expired_note_cannot_be_approved"] = True
    report = {"checked_at": now.isoformat(), "knowledge_base_id": kb["id"],
        "note_ids": note_ids, "document_ids": ids, "acceptance_checks": checks,
        "acceptance_passed": all(checks.values()), "retrieval_evaluation": evaluation,
        "limitations": ["全部是本人编写的合成样本，不是实际资讯或独立盲测。",
            "采用正在运行的检索配置；回答使用本地摘录，未调用生成模型。",
            "撤销后可能召回其他不相关文档；这里只检查被撤销证据不再参与，相关性另按标注评测。",
            "验收资料保留在独立知识库中，避免混入默认用户资料。"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"acceptance_passed": report["acceptance_passed"], "checks": checks,
        "metrics": evaluation["metrics"], "knowledge_base_id": kb["id"], "output": str(args.output)}, ensure_ascii=False, indent=2))
    if not report["acceptance_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Smoke test for the current RAG-backed workflow through legacy API routes.

Prerequisites:
- Backend is running on http://127.0.0.1:8000
- DATABASE_URL points to the local development database

The script creates temporary source data and a dedicated knowledge
base, checks the allowlisted tool API, starts an Agent Run with RAG enabled,
checks awaiting_review (not approval or publishing), and checks refusal.
Embedding and vector operations stay in the backend process. The generation
provider is explicitly local: this does not test an external generation model.
Retrieval mode comes from the backend configuration, never a fake vector label.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.db.session import SessionLocal  # noqa: E402
from app.models.agent_run import AgentRun, AgentStep  # noqa: E402
from app.models.card import Card  # noqa: E402
from app.models.draft import Draft  # noqa: E402
from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument  # noqa: E402
from app.models.source import Source  # noqa: E402
from app.models.topic import Topic  # noqa: E402


SAMPLE_SOURCE = """\
LangChain 在内容增长 Agent 里应该作为工具和文档处理层，而不是替换全部业务服务。
RAG 的核心价值是把已入库素材变成可引用证据，所有检索必须限制在 workspace_id 和 knowledge_base_id 内。
当知识库证据不足时，Agent 应该提示人工补来源，或把输出降级为观点草稿。
当前 LangGraph 实际执行内容工作流与条件分支，SQL 保存节点检查点，草稿最后等待人工审核。
这是本地单进程演示，没有分布式 worker；审核状态也不是登录身份认证。
"""


def request_json(base_url: str, path: str, body: dict | None = None, timeout: int = 180, method: str | None = None) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    if body is None and method is None:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.load(resp)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method or "POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def cleanup(base_url: str, db, source_id: int | None, run_id: int | None, knowledge_base_id: int | None) -> None:
    db.rollback()
    db.expire_all()
    if source_id:
        docs = [(doc.id, doc.knowledge_base_id) for doc in db.query(KnowledgeDocument).filter(KnowledgeDocument.source_id == source_id)]
        db.rollback()
        for doc_id, kb_id in docs:
            result = request_json(base_url, f"/api/knowledge/documents/{doc_id}?knowledge_base_id={kb_id}", method="DELETE")
            if result.get("vector_cleanup_pending"):
                raise RuntimeError("Smoke document SQL was removed but vector cleanup needs attention")
    if run_id:
        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if run:
            if run.draft_id:
                db.query(Card).filter(Card.draft_id == run.draft_id).delete(synchronize_session=False)
                db.query(Draft).filter(Draft.id == run.draft_id).delete(synchronize_session=False)
            if run.selected_topic_id:
                topic = db.query(Topic).filter(Topic.id == run.selected_topic_id).first()
                topic_source_id = topic.source_id if topic else None
                db.query(Topic).filter(Topic.id == run.selected_topic_id).delete(synchronize_session=False)
                if topic_source_id:
                    db.query(Source).filter(Source.id == topic_source_id).delete(synchronize_session=False)
            db.query(AgentStep).filter(AgentStep.run_id == run_id).delete(synchronize_session=False)
            db.query(AgentRun).filter(AgentRun.id == run_id).delete(synchronize_session=False)

    if source_id:
        db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
    if knowledge_base_id:
        db.query(KnowledgeBase).filter(KnowledgeBase.id == knowledge_base_id).delete(synchronize_session=False)
    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--poll-seconds", type=int, default=45)
    parser.add_argument("--require-semantic", action="store_true", help="Fail unless backend explicitly uses semantic retrieval")
    args = parser.parse_args()

    db = SessionLocal()
    source_id: int | None = None
    run_id: int | None = None
    knowledge_base_id: int | None = None
    safe_to_cleanup = True
    try:
        request_json(args.backend_url, "/health")
        retrieval = request_json(args.backend_url, "/api/knowledge/status")
        if not retrieval.get("configured"):
            raise RuntimeError("Backend retrieval configuration is invalid or dependencies are missing")
        if args.require_semantic and retrieval.get("mode") != "semantic":
            raise RuntimeError("Backend is not configured for semantic retrieval")
        knowledge_base = request_json(args.backend_url, "/api/v04/knowledge-bases", {
            "name": f"Smoke temporary {uuid.uuid4().hex[:8]}",
            "purpose": "临时自测资料，仅用于本次检索和工作流检查",
        })
        knowledge_base_id = knowledge_base["id"]

        source = Source(
            source_type="manual",
            title="__v04_smoke_rag_agent__",
            url="",
            raw_content=SAMPLE_SOURCE,
            summary=SAMPLE_SOURCE[:220],
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_id = source.id

        index_result = request_json(args.backend_url, "/api/v04/rag/index-source", {
            "source_id": source_id, "knowledge_base_id": knowledge_base_id,
        })
        tools = request_json(args.backend_url, "/api/v04/tools")
        tool_names = {item["name"] for item in tools.get("items") or []}
        required_tools = {"rag.search", "rag.answer", "source.index"}
        if not required_tools.issubset(tool_names):
            raise RuntimeError(f"Missing v0.4 tools: {sorted(required_tools - tool_names)}")

        tool_search = request_json(args.backend_url, "/api/v04/tools/execute", {
            "tool_name": "rag.search",
            "knowledge_base_id": index_result["knowledge_base_id"],
            "arguments": {
                "query": "workspace_id knowledge_base_id RAG 检索边界",
                "top_k": 3,
            },
        })
        tool_items = ((tool_search.get("output") or {}).get("items") or [])
        if not tool_items:
            raise RuntimeError("Allowlisted rag.search tool did not return evidence")

        run = request_json(args.backend_url, "/api/agent-runs", {
            "goal": "LangChain、RAG 和 LangGraph 应该怎么接进内容增长 Agent？",
            "mode": "inspiration",
            "research_depth": "quick",
            "target_audience": "AI 新手 / 自媒体人",
            "viewpoint": "说明检索证据、工作流检查点和人工审核的边界",
            "content_type": "tutorial",
            "provider": "local",
            "auto_score": True,
            "use_rag": True,
            "knowledge_base_id": index_result["knowledge_base_id"],
            "rag_top_k": 5,
        })
        run_id = run["id"]
        safe_to_cleanup = False

        detail = run
        for _ in range(args.poll_seconds):
            detail = request_json(args.backend_url, f"/api/agent-runs/{run_id}")
            if detail["status"] not in {"pending", "running"}:
                safe_to_cleanup = True
                break
            time.sleep(1)

        rag_context = (detail.get("result_json") or {}).get("rag_context") or {}
        steps = {item["key"]: item["status"] for item in detail.get("steps") or []}
        if detail["status"] != "awaiting_review":
            raise RuntimeError(f"Agent run did not reach awaiting_review: {detail['status']} / {detail.get('error_message')}")
        if steps.get("human_review") != "awaiting_review":
            raise RuntimeError("Human review gate was not persisted")
        if steps.get("retrieve_context") != "completed":
            raise RuntimeError(f"retrieve_context step failed: {steps}")
        if not rag_context.get("hits"):
            raise RuntimeError("RAG context did not include evidence hits")

        refusal = request_json(args.backend_url, "/api/v04/rag/answer", {
            "query": "火星探测器采用什么轨道转移方案？",
            "knowledge_base_id": knowledge_base_id, "provider": "local",
        })
        if not refusal.get("refused"):
            raise RuntimeError("Out-of-domain question was not refused")

        print(json.dumps({
            "ok": True,
            "run_id": run_id,
            "retrieve_context": steps.get("retrieve_context"),
            "evidence_status": rag_context.get("evidence_status"),
            "hit_count": len(rag_context.get("hits") or []),
            "chunk_count": index_result["chunk_count"],
            "status": detail["status"],
            "generation_mode": "local_rule_demo",
            "retrieval_mode": retrieval["mode"],
            "embedding_model": retrieval.get("embedding_model"),
            "embedding_provider": retrieval.get("embedding_provider"),
            "tool_count": tools.get("total"),
            "tool_search_hits": len(tool_items),
            "refusal_reason": refusal.get("refusal_reason"),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        if safe_to_cleanup:
            cleanup(args.backend_url, db, source_id, run_id, knowledge_base_id)
        else:
            print(f"Run {run_id} is still active; temporary KB {knowledge_base_id} is retained to avoid deleting live task data.", file=sys.stderr)
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

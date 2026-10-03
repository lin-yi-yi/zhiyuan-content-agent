"""Local project overview; only fixed public research artifacts are exposed."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.llm.router import router as llm_router
from app.models.agent_run import AgentRun
from app.models.knowledge_base import KnowledgeDocument, KnowledgeChunk
from app.models.draft import Draft
from app.saas.context import is_saas_mode

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
ROOT = Path(__file__).resolve().parents[4]


def demo_dataset():
    return json.loads((ROOT / "scripts/fixtures/rag_demo.json").read_text())


@router.post("/demo")
def load_demo(db: Session = Depends(get_db)):
    from app.api.routes.knowledge import DocumentUpload, upload_document
    from app.agent_core.boundaries import workspace_context, get_knowledge_base_or_default
    context = workspace_context(db)
    kb = get_knowledge_base_or_default(db, context)
    for item in demo_dataset()["documents"]:
        current = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.workspace_id == context.workspace_id,
            KnowledgeDocument.knowledge_base_id == kb.id,
            KnowledgeDocument.source_uri == item["source_uri"],
        ).first()
        upload_document(DocumentUpload(
            title=item["title"], content=item["content"], source_uri=item["source_uri"],
            document_id=current.id if current else None,
            workspace_id=context.workspace_id, knowledge_base_id=kb.id,
        ), db)
    return {"documents": len(demo_dataset()["documents"]), "label": "人工编写演示资料"}


@router.get("/evaluation-cases")
def evaluation_cases(db: Session = Depends(get_db)):
    from app.agent_core.boundaries import workspace_context, get_knowledge_base_or_default
    context = workspace_context(db)
    kb = get_knowledge_base_or_default(db, context)
    dataset = demo_dataset()
    keys = {}
    for item in dataset["documents"]:
        document = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.workspace_id == context.workspace_id,
            KnowledgeDocument.knowledge_base_id == kb.id,
            KnowledgeDocument.source_uri == item["source_uri"],
        ).first()
        if not document:
            raise HTTPException(409, "请先在项目总览载入演示资料，再运行演示评测。")
        keys[item["key"]] = document.id
    return {"cases": [{
        "question": case["question"],
        "expected_document_ids": [keys[key] for key in case.get("expected_document_keys", [])],
        "should_refuse": case.get("should_refuse", False),
    } for case in dataset["cases"]], "dataset_id": dataset["dataset_id"]}


@router.get("")
def overview(db: Session = Depends(get_db)):
    jobs_file = ROOT / "docs/boss-shenzhen-jobs-2026-09-19.json"
    research = json.loads(jobs_file.read_text()) if jobs_file.exists() else {"jobs": []}
    jobs = []
    for item in research["jobs"]:
        record = dict(item)
        for field in ("education", "experience"):
            value = record.get(field)
            record[field] = value.get("label", "未知") if isinstance(value, dict) else value or "未知"
        jobs.append(record)
    return {
        "version": "0.7.0",
        "scope": ("SaaS 单机小团队试点；已实现账号、组织隔离与角色权限，尚未完成公网生产验收。"
                  if is_saas_mode() else "本地免登录功能开发模式；知识、内容生成与复盘保存在本机。"),
        "counts": {
            "documents": db.query(KnowledgeDocument).count(),
            "chunks": db.query(KnowledgeChunk).count(),
            "runs": db.query(AgentRun).count(),
            "awaiting_review": db.query(AgentRun).filter(AgentRun.status == "awaiting_review").count(),
            "drafts": db.query(Draft).count(),
        },
        "providers": [{k: p[k] for k in ("provider", "model", "configured")} for p in llm_router.list_available_providers()],
        "jobs": jobs,
        "jobs_note": f"{len(jobs)} 条 BOSS 岗位参考。分别标注列表摘要、部分正文或完整正文；登录限制下未读到的要求保持未知。页面招聘状态不等于招聘方确认，小样本不代表整个市场。",
        "latest_evaluation": None,
    }

"""Backward-compatible foundation endpoints for the current local workbench."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent_core.boundaries import (
    get_default_workspace,
    get_knowledge_base_or_default,
    list_capabilities,
    workspace_context,
)
from app.agent_core.langchain_adapter import framework_status
from app.agent_core.embeddings import retrieval_status
from app.agent_core.rag_service import answer_question, index_source, search_knowledge
from app.agent_core.tools import execute_tool, list_tools
from app.db.session import get_db
from app.models.knowledge_base import KnowledgeBase, KnowledgeChunk
from app.models.workspace import Workspace
from app.schemas.evidence import RequiredFacts

router = APIRouter(prefix="/v04", tags=["v0.4-agent-foundation"])


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    workspace_id: int | None = None
    purpose: str = ""
    boundary_notes: str = ""


class RagIndexSourceRequest(BaseModel):
    source_id: int
    workspace_id: int | None = None
    knowledge_base_id: int | None = None
    ingestion_profile: str = "default"


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    workspace_id: int | None = None
    knowledge_base_id: int | None = None
    top_k: int = Field(5, ge=1, le=12)
    min_score: float = Field(0.08, ge=0, le=1)
    retrieval_mode: Literal["lexical", "semantic", "hybrid"] | None = None


class RagAnswerRequest(RagSearchRequest):
    provider: str = "local"
    model: str = ""
    required_facts: RequiredFacts = Field(default_factory=list)


class ToolExecuteRequest(BaseModel):
    tool_name: str = Field(..., min_length=1, max_length=120)
    workspace_id: int | None = None
    knowledge_base_id: int | None = None
    arguments: dict = Field(default_factory=dict)


@router.get("/architecture")
def architecture(db: Session = Depends(get_db)):
    workspace = get_default_workspace(db)
    knowledge_base = get_knowledge_base_or_default(db, workspace_context(db, workspace.id))
    retrieval = retrieval_status()
    mode = retrieval.get("mode")
    dimensions = set()
    if mode in {"semantic", "hybrid"}:
        dimensions = {chunk.embedding_dim for chunk in db.query(KnowledgeChunk).filter(
            KnowledgeChunk.workspace_id == workspace.id,
            KnowledgeChunk.knowledge_base_id == knowledge_base.id,
            KnowledgeChunk.embedding_provider == retrieval.get("embedding_provider"),
            KnowledgeChunk.embedding_model == retrieval.get("embedding_model"),
            KnowledgeChunk.embedding_dim > 0,
        ).all() if (chunk.metadata_json or {}).get("index_fingerprint") == retrieval.get("index_fingerprint")}
    strategy_names = {
        "semantic": "语义检索（已配置）" if retrieval.get("configured") else "语义检索（依赖不完整）",
        "hybrid": "混合检索：BM25 + 向量 + RRF（已配置）" if retrieval.get("configured") else "混合检索（依赖不完整）",
        "lexical": "词项检索演示", "invalid": "检索配置错误",
    }
    from app.saas.context import is_saas_mode
    saas = is_saas_mode()
    capabilities = list_capabilities()
    for capability in capabilities:
        if capability["name"] == "source_read":
            capability["data_scope"] = "组织独立业务库；索引和检索继续按知识库范围过滤。" if saas else "本地共享素材表；知识库索引和检索按 workspace_id + knowledge_base_id 过滤。"
            capability["limitations"] = ["不读取本机任意文件", "登录会话与成员角色校验" if saas else "本地单用户功能，未实现身份认证和用户授权"]
    return {
        "version": "v0.7-saas-pilot" if saas else "v0.6-local-workbench",
        "product_boundary": {
            "product": "AI 内容增长 Agent",
            "primary_scenario": "资料导入 → 证据检索 → 引用式草稿 → 人工审核 → 导出内容。",
            "in_scope": [
                "文本和 Markdown 入库、去重、更新与索引重建",
                "可选语义检索、BM25与向量RRF融合、明确标记的词项演示",
                "LangGraph 工作流、SQL 节点检查点和显式重试",
                "证据不足拒答、引用 ID 检查和人工审核状态",
                "OpenAI-compatible 模型接口与明确标记的本地规则演示",
            ],
            "out_of_scope": [
                "自动发布到外部平台",
                "读取本机任意文件",
                "访问浏览器 Cookie、账号密码或外部密钥",
                "跨 workspace 混用知识库数据",
                "邮件验证、密码找回和多因素认证" if saas else "登录身份认证与角色授权",
                "分布式 worker 与多进程任务调度",
            ],
        },
        "data_isolation": {
            "default_workspace_id": workspace.id,
            "default_knowledge_base_id": knowledge_base.id,
            "rule": "服务端登录会话和组织成员关系决定独立数据库与向量目录；修改 workspace_id 不能切换组织。" if saas else "本地单用户应用。RAG 使用 workspace_id + knowledge_base_id 过滤资料范围；这些可由请求指定的 ID 不构成用户身份或访问授权。",
            "tables": ["workspaces", "knowledge_bases", "knowledge_documents", "knowledge_chunks", "agent_runs", "agent_steps"],
            "legacy_tables": "sources 保存原始素材；topics/drafts/cards 保存内容成果。知识库更新会重建索引，删除文档保留旧素材记录。",
        },
        "retrieval_strategy": {
            "name": strategy_names.get(mode, "检索状态未知"),
            "embedding_provider": retrieval.get("embedding_provider") or "未使用",
            "embedding_model": retrieval.get("embedding_model") or "未使用",
            # Zero means unknown/not applicable; never invent dimensions from a model name.
            "embedding_dim": next(iter(dimensions)) if len(dimensions) == 1 else 0,
            "scoring": "BM25 + Qdrant余弦双路召回，RRF排序，独立证据门控" if mode == "hybrid" else "真实 Embedding + Qdrant 本地余弦检索" if mode == "semantic" else (
                "词项重叠评分；不生成向量" if mode == "lexical" else "配置无效，检索不会静默降级"),
            "limitation": " ".join(retrieval.get("limitations") or []) + " 页面读取配置与已存索引元数据，不执行实时检索验收。",
        },
        "capabilities": capabilities,
        "tools": list_tools(),
        "framework_status": framework_status(),
        "workflow_boundary": {
            "current": "LangGraph 实际执行检索、生成、评估与至多一次规则修订分支，最后进入 awaiting_review。人工通过或退回记录为 approved / rejected。",
            "v04_extension": "AgentRun / AgentStep 保存 SQL 节点检查点。失败后显式重试复用已完成步骤；人工审核是数据库状态，不是 LangGraph 原生 interrupt/checkpointer。",
            "async_boundary": "单进程后台任务携带服务端组织上下文；重启后首次访问组织时标记中断任务以便人工重试。无分布式队列。" if saas else "FastAPI BackgroundTasks 在单进程内执行；启动时标记中断任务供人工重试。没有分布式 worker、持久化消息队列或登录身份认证；本地 Qdrant 也要求单进程。",
        },
    }


@router.get("/workspaces")
def list_workspaces(db: Session = Depends(get_db)):
    items = db.query(Workspace).order_by(Workspace.id).all()
    return [{
        "id": item.id,
        "name": item.name,
        "slug": item.slug,
        "description": item.description,
        "data_boundary": item.data_boundary,
        "is_default": item.is_default,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    } for item in items]


@router.get("/knowledge-bases")
def list_knowledge_bases(
    workspace_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    try:
        context = workspace_context(db, workspace_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    items = (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.workspace_id == context.workspace_id)
        .order_by(KnowledgeBase.id)
        .all()
    )
    return [_knowledge_base_out(item) for item in items]


@router.post("/knowledge-bases", status_code=201)
def create_knowledge_base(body: KnowledgeBaseCreate, db: Session = Depends(get_db)):
    try:
        context = workspace_context(db, body.workspace_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    item = KnowledgeBase(
        workspace_id=context.workspace_id,
        name=body.name.strip(),
        purpose=body.purpose.strip() or None,
        boundary_notes=body.boundary_notes.strip() or "仅允许当前 workspace 内检索。",
        status="active",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _knowledge_base_out(item)


@router.post("/rag/index-source", status_code=201)
def index_source_endpoint(body: RagIndexSourceRequest, db: Session = Depends(get_db)):
    try:
        context = workspace_context(db, body.workspace_id)
        return index_source(
            source_id=body.source_id,
            db=db,
            context=context,
            knowledge_base_id=body.knowledge_base_id,
            ingestion_profile=body.ingestion_profile,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/rag/search")
def search_endpoint(body: RagSearchRequest, db: Session = Depends(get_db)):
    try:
        context = workspace_context(db, body.workspace_id)
        hits = search_knowledge(
            query=body.query,
            db=db,
            context=context,
            knowledge_base_id=body.knowledge_base_id,
            top_k=body.top_k,
            min_score=body.min_score,
            retrieval_mode=body.retrieval_mode,
        )
        status = retrieval_status(body.retrieval_mode)
        return {"items": [item.to_dict() for item in hits], "total": len(hits),
                "strategy": status.get("strategy"), "retrieval": status}
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/rag/answer")
def answer_endpoint(body: RagAnswerRequest, db: Session = Depends(get_db)):
    try:
        context = workspace_context(db, body.workspace_id)
        return answer_question(
            question=body.query,
            db=db,
            context=context,
            knowledge_base_id=body.knowledge_base_id,
            provider=body.provider,
            model=body.model,
            top_k=body.top_k,
            retrieval_mode=body.retrieval_mode,
            required_facts=body.required_facts,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/tools")
def tools_endpoint():
    tools = list_tools()
    return {"items": tools, "total": len(tools)}


@router.post("/tools/execute")
def execute_tool_endpoint(body: ToolExecuteRequest, db: Session = Depends(get_db)):
    try:
        context = workspace_context(db, body.workspace_id)
        return execute_tool(
            tool_name=body.tool_name,
            arguments=body.arguments,
            db=db,
            context=context,
            knowledge_base_id=body.knowledge_base_id,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _knowledge_base_out(item: KnowledgeBase) -> dict:
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "name": item.name,
        "purpose": item.purpose,
        "boundary_notes": item.boundary_notes,
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }

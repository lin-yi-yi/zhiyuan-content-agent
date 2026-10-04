"""Allowlisted internal tools for bounded v0.4 function calling."""
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.agent_core.boundaries import (
    WorkspaceContext,
    get_knowledge_base_or_default,
    require_capability,
)
from app.agent_core.rag_service import answer_question, index_source, search_knowledge
from app.agent_core.embeddings import retrieval_status
from app.schemas.evidence import RequiredFacts


class RagSearchArguments(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(5, ge=1, le=12)
    min_score: float = Field(0.08, ge=0, le=1)
    retrieval_mode: Literal["lexical", "semantic", "hybrid"] | None = None


class RagAnswerArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=1000)
    provider: str = Field("local", max_length=80)
    model: str = Field("", max_length=160)
    top_k: int = Field(5, ge=1, le=12)
    retrieval_mode: Literal["lexical", "semantic", "hybrid"] | None = None
    required_facts: RequiredFacts = Field(default_factory=list)


class SourceIndexArguments(BaseModel):
    source_id: int = Field(..., ge=1)
    ingestion_profile: str = Field("default", max_length=80)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    description: str
    category: str
    capability: str
    action: str
    data_scope: str
    writes: bool = False
    boundary_rules: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "rag.search": ToolSpec(
        name="rag.search",
        label="知识库检索",
        description="在当前 workspace 和 knowledge base 内检索证据 chunks。",
        category="rag",
        capability="rag_retrieve",
        action="search",
        data_scope="knowledge_chunks scoped by workspace_id + knowledge_base_id",
        boundary_rules=[
            "必须提供自然语言 query",
            "最多返回 12 条证据",
            "不读取本机文件、浏览器状态或外部账号",
        ],
        input_schema=RagSearchArguments.model_json_schema(),
    ),
    "rag.answer": ToolSpec(
        name="rag.answer",
        label="证据问答",
        description="在当前资料范围内先检索证据；可用 required_facts 显式列出型号与所问参数，缺项时在模型生成前拒答。",
        category="rag",
        capability="rag_retrieve",
        action="cite",
        data_scope="knowledge_chunks scoped by workspace_id + knowledge_base_id",
        boundary_rules=[
            "答案必须来自检索证据",
            "证据不足时返回 refused=true",
            "required_facts 最多 10 项；每项包含 product_model 和 parameter，缺项参数也应如实填写",
            "必需事实只在当前 workspace 和 knowledge base 的有效检索摘录中核对，不跨范围补证",
            "未传 required_facts 时 answerability=not_assessed，不代表已检查问题的全部事实",
            "拒绝未知参数字段，避免显式要求被静默忽略",
            "模型调用只接收最小必要证据上下文",
        ],
        input_schema=RagAnswerArguments.model_json_schema(),
    ),
    "source.index": ToolSpec(
        name="source.index",
        label="素材入库",
        description="把已有 sources 记录切块写入指定知识库。",
        category="ingestion",
        capability="source_index",
        action="index",
        data_scope="read sources by source_id, write knowledge_documents/chunks in current scope",
        writes=True,
        boundary_rules=[
            "只索引数据库 sources 表中已有素材",
            "不读取任意本地路径或外部 URL",
            "重建同一 source 的索引前会删除同范围旧 chunks",
        ],
        input_schema=SourceIndexArguments.model_json_schema(),
    ),
}

TOOL_ARGUMENTS: dict[str, type[BaseModel]] = {
    "rag.search": RagSearchArguments,
    "rag.answer": RagAnswerArguments,
    "source.index": SourceIndexArguments,
}


def list_tools() -> list[dict]:
    return [item.to_dict() for item in TOOL_REGISTRY.values()]


def execute_tool(
    tool_name: str,
    arguments: dict[str, Any] | None,
    db: Session,
    context: WorkspaceContext,
    knowledge_base_id: int | None = None,
) -> dict:
    spec = TOOL_REGISTRY.get(tool_name)
    if not spec:
        raise PermissionError(f"工具不在白名单: {tool_name}")
    require_capability(spec.capability, spec.action)

    args = _parse_arguments(tool_name, arguments or {})
    knowledge_base = get_knowledge_base_or_default(db, context, knowledge_base_id)

    if tool_name == "rag.search":
        output = _execute_rag_search(args, db, context, knowledge_base.id)
    elif tool_name == "rag.answer":
        output = _execute_rag_answer(args, db, context, knowledge_base.id)
    elif tool_name == "source.index":
        output = _execute_source_index(args, db, context, knowledge_base.id)
    else:
        raise PermissionError(f"工具尚未绑定执行器: {tool_name}")

    return {
        "tool_name": spec.name,
        "label": spec.label,
        "capability": spec.capability,
        "action": spec.action,
        "workspace_id": context.workspace_id,
        "knowledge_base_id": knowledge_base.id,
        "writes": spec.writes,
        "boundary": {
            "data_scope": spec.data_scope,
            "rules": spec.boundary_rules,
        },
        "output": output,
    }


def _parse_arguments(tool_name: str, arguments: dict[str, Any]) -> BaseModel:
    model = TOOL_ARGUMENTS.get(tool_name)
    if not model:
        raise PermissionError(f"工具参数模型未注册: {tool_name}")
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid arguments"}
        location = ".".join(str(item) for item in first.get("loc", []))
        message = first.get("msg", "invalid arguments")
        raise ValueError(f"工具参数不合法: {location} {message}".strip()) from exc


def _execute_rag_search(
    args: BaseModel,
    db: Session,
    context: WorkspaceContext,
    knowledge_base_id: int,
) -> dict:
    parsed = args if isinstance(args, RagSearchArguments) else RagSearchArguments.model_validate(args)
    hits = search_knowledge(
        query=parsed.query,
        db=db,
        context=context,
        knowledge_base_id=knowledge_base_id,
        top_k=parsed.top_k,
        min_score=parsed.min_score,
        retrieval_mode=parsed.retrieval_mode,
    )
    return {
        "items": [item.to_dict() for item in hits],
        "total": len(hits),
        "strategy": retrieval_status(parsed.retrieval_mode).get("strategy"),
        "retrieval": retrieval_status(parsed.retrieval_mode),
    }


def _execute_rag_answer(
    args: BaseModel,
    db: Session,
    context: WorkspaceContext,
    knowledge_base_id: int,
) -> dict:
    parsed = args if isinstance(args, RagAnswerArguments) else RagAnswerArguments.model_validate(args)
    return answer_question(
        question=parsed.query,
        db=db,
        context=context,
        knowledge_base_id=knowledge_base_id,
        provider=parsed.provider or "local",
        model=parsed.model or "",
        top_k=parsed.top_k,
        retrieval_mode=parsed.retrieval_mode,
        required_facts=parsed.required_facts,
    )


def _execute_source_index(
    args: BaseModel,
    db: Session,
    context: WorkspaceContext,
    knowledge_base_id: int,
) -> dict:
    parsed = args if isinstance(args, SourceIndexArguments) else SourceIndexArguments.model_validate(args)
    return index_source(
        source_id=parsed.source_id,
        db=db,
        context=context,
        knowledge_base_id=knowledge_base_id,
        ingestion_profile=parsed.ingestion_profile or "default",
    )

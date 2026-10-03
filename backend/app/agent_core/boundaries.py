"""Data isolation and capability boundaries for v0.4 Agent work."""
from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session

from app.models.knowledge_base import KnowledgeBase
from app.models.workspace import Workspace


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: int
    workspace_slug: str
    actor: str = "local_user"


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    label: str
    category: str
    access_level: str
    allowed_actions: list[str]
    data_scope: str
    can_write: bool = False
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


CAPABILITY_REGISTRY: dict[str, CapabilitySpec] = {
    "source_read": CapabilitySpec(
        name="source_read",
        label="读取素材库",
        category="data",
        access_level="workspace",
        allowed_actions=["list", "read"],
        data_scope="当前 workspace 下已入库的 sources / knowledge_chunks",
        limitations=["不读取本机任意文件", "不跨 workspace 查询"],
    ),
    "rag_retrieve": CapabilitySpec(
        name="rag_retrieve",
        label="RAG 检索",
        category="rag",
        access_level="knowledge_base",
        allowed_actions=["search", "cite"],
        data_scope="指定 knowledge_base_id 内的 chunks",
        limitations=["证据不足时必须拒答", "答案必须返回引用 chunk"],
    ),
    "source_index": CapabilitySpec(
        name="source_index",
        label="素材入知识库",
        category="rag",
        access_level="knowledge_base",
        allowed_actions=["index"],
        data_scope="把已有 sources.raw_content / summary 切块写入 knowledge_chunks",
        can_write=True,
        limitations=["只索引数据库素材", "不写入旧版内容生产表"],
    ),
    "content_generate": CapabilitySpec(
        name="content_generate",
        label="内容生成",
        category="generation",
        access_level="workspace",
        allowed_actions=["draft", "cards", "evaluate"],
        data_scope="当前 workspace 的内容任务上下文",
        can_write=True,
        limitations=["生成结果仍需人工审核", "不自动发布到外部平台"],
    ),
    "model_call": CapabilitySpec(
        name="model_call",
        label="模型调用",
        category="llm",
        access_level="configured_provider",
        allowed_actions=["chat", "json", "answer"],
        data_scope="传入 prompt 的最小必要上下文",
        limitations=["不回显 API Key", "不把未检索到的事实当作依据"],
    ),
}


def list_capabilities() -> list[dict]:
    return [item.to_dict() for item in CAPABILITY_REGISTRY.values()]


def require_capability(name: str, action: str) -> CapabilitySpec:
    spec = CAPABILITY_REGISTRY.get(name)
    if not spec:
        raise PermissionError(f"未知能力: {name}")
    if action not in spec.allowed_actions:
        raise PermissionError(f"能力 {name} 不允许动作: {action}")
    return spec


def get_default_workspace(db: Session) -> Workspace:
    workspace = db.query(Workspace).filter(Workspace.is_default.is_(True)).order_by(Workspace.id).first()
    if workspace:
        return workspace
    from app.saas.context import is_saas_mode
    saas = is_saas_mode()
    workspace = Workspace(
        name="默认工作区",
        slug="default",
        description="组织知识与内容工作区。" if saas else "本地单用户内容增长与 RAG 实验工作区。",
        data_boundary="仅当前已授权组织；组织拥有独立业务库和向量目录，知识库继续按范围过滤。" if saas else "仅用于本机项目数据；RAG 检索必须限制在当前 workspace_id 内。",
        is_default=True,
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


def get_workspace_or_default(db: Session, workspace_id: int | None = None) -> Workspace:
    if workspace_id:
        workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        if not workspace:
            raise ValueError("workspace 不存在")
        return workspace
    return get_default_workspace(db)


def workspace_context(db: Session, workspace_id: int | None = None) -> WorkspaceContext:
    workspace = get_workspace_or_default(db, workspace_id)
    from app.saas.context import current_tenant
    tenant = current_tenant.get()
    return WorkspaceContext(workspace_id=workspace.id, workspace_slug=workspace.slug,
                            actor=tenant.user_id if tenant else "local_user")


def assert_workspace_scope(context: WorkspaceContext, workspace_id: int) -> None:
    if context.workspace_id != workspace_id:
        raise PermissionError("拒绝跨 workspace 访问数据")


def get_knowledge_base_or_default(
    db: Session,
    context: WorkspaceContext,
    knowledge_base_id: int | None = None,
) -> KnowledgeBase:
    if knowledge_base_id:
        knowledge_base = (
            db.query(KnowledgeBase)
            .filter(KnowledgeBase.id == knowledge_base_id, KnowledgeBase.workspace_id == context.workspace_id)
            .first()
        )
        if not knowledge_base:
            raise ValueError("knowledge_base 不存在或不属于当前 workspace")
        return knowledge_base

    knowledge_base = (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.workspace_id == context.workspace_id, KnowledgeBase.status == "active")
        .order_by(KnowledgeBase.id)
        .first()
    )
    if knowledge_base:
        return knowledge_base
    knowledge_base = KnowledgeBase(
        workspace_id=context.workspace_id,
        name="内容素材知识库",
        purpose="把素材库中的资料切块后用于 RAG 检索和内容生成引用。",
        boundary_notes="只索引 sources 表中的素材内容，不读取用户本机任意文件或外部凭证。",
        status="active",
    )
    db.add(knowledge_base)
    db.commit()
    db.refresh(knowledge_base)
    return knowledge_base

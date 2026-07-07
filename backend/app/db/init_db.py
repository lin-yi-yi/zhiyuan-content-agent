"""数据库初始化 — 创建所有表并补齐轻量字段迁移。"""
from sqlalchemy import inspect, text

from app.db.session import engine, Base
import app.models  # noqa: F401 - 确保所有模型注册到 Base.metadata
from app.models.knowledge_base import KnowledgeBase
from app.models.workspace import Workspace
from app.db.session import SessionLocal


def init_database():
    """创建所有 SQLAlchemy 模型对应的表"""
    Base.metadata.create_all(bind=engine)
    _ensure_default_workspace()
    _ensure_draft_variant_columns()
    _ensure_v04_rag_columns()
    _ensure_weekly_report_columns()


def _ensure_default_workspace():
    """v0.4 初始化：为单用户本地项目创建默认数据边界。"""
    db = SessionLocal()
    try:
        workspace = db.query(Workspace).filter(Workspace.slug == "default").first()
        if not workspace:
            workspace = Workspace(
                name="默认工作区",
                slug="default",
                description="本地单用户内容增长与 RAG 实验工作区。",
                data_boundary="仅用于本机项目数据；RAG 检索必须限制在当前 workspace_id 内。",
                is_default=True,
            )
            db.add(workspace)
            db.commit()
            db.refresh(workspace)
        elif not workspace.is_default:
            workspace.is_default = True
            db.commit()

        knowledge_base = (
            db.query(KnowledgeBase)
            .filter(KnowledgeBase.workspace_id == workspace.id, KnowledgeBase.name == "内容素材知识库")
            .first()
        )
        if not knowledge_base:
            db.add(KnowledgeBase(
                workspace_id=workspace.id,
                name="内容素材知识库",
                purpose="把素材库中的 URL/GitHub/手动资料切块后用于 RAG 检索和内容生成引用。",
                boundary_notes="只索引 sources 表中的素材内容，不读取用户本机任意文件或外部凭证。",
                status="active",
            ))
            db.commit()
    finally:
        db.close()


def _ensure_draft_variant_columns():
    """v0.2-J 轻量迁移：旧 MySQL drafts 表补齐发布方案字段。"""
    inspector = inspect(engine)
    if "drafts" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("drafts")}
    columns = {
        "variant_name": "VARCHAR(255) NULL",
        "selected_title": "VARCHAR(255) NULL",
        "selected_cover_text": "VARCHAR(255) NULL",
        "body_variant_key": "VARCHAR(50) NULL",
        "body_variants": "JSON NULL",
        "content_type": "VARCHAR(100) NULL",
        "template_key": "VARCHAR(100) NULL",
        "theme_key": "VARCHAR(100) NULL",
        "max_card_count": "INT NULL",
        "generated_reason": "TEXT NULL",
    }
    missing = [(name, definition) for name, definition in columns.items() if name not in existing]
    if not missing:
        return
    with engine.begin() as conn:
        for name, definition in missing:
            conn.execute(text(f"ALTER TABLE drafts ADD COLUMN {name} {definition}"))


def _ensure_v04_rag_columns():
    """v0.4-C 轻量迁移：为本地向量检索补齐 chunk embedding 字段。"""
    inspector = inspect(engine)
    if "knowledge_chunks" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("knowledge_chunks")}
    columns = {
        "embedding_dim": "INT NULL",
        "embedding_json": "JSON NULL",
    }
    missing = [(name, definition) for name, definition in columns.items() if name not in existing]
    if not missing:
        return
    with engine.begin() as conn:
        for name, definition in missing:
            conn.execute(text(f"ALTER TABLE knowledge_chunks ADD COLUMN {name} {definition}"))


def _ensure_weekly_report_columns():
    """v0.5 轻量迁移：补齐复盘聚合和预测校准字段。"""
    inspector = inspect(engine)
    if "weekly_reports" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("weekly_reports")}
    columns = {
        "angle_performance": "JSON NULL",
        "content_type_performance": "JSON NULL",
        "template_performance": "JSON NULL",
        "performance_summary": "JSON NULL",
        "prediction_calibration": "JSON NULL",
    }
    missing = [(name, definition) for name, definition in columns.items() if name not in existing]
    if not missing:
        return
    with engine.begin() as conn:
        for name, definition in missing:
            conn.execute(text(f"ALTER TABLE weekly_reports ADD COLUMN {name} {definition}"))


if __name__ == "__main__":
    init_database()
    print("数据库初始化完成")

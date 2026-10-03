"""数据库会话管理"""
from sqlalchemy import create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy import BigInteger
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from threading import RLock

from app.core.config import settings

@compiles(BigInteger, "sqlite")
def _sqlite_bigint(type_, compiler, **kw):
    # SQLite auto-increment requires the exact INTEGER type name.
    return "INTEGER"


engine = create_engine(
    settings.DATABASE_URL, pool_pre_ping=True, pool_recycle=3600,
    connect_args={"check_same_thread": False, "timeout": 30} if settings.DATABASE_URL.startswith("sqlite") else {},
)
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
_local_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


_tenant_stores = {}
_tenant_store_lock = RLock()


def tenant_session(organization_id: str):
    """Business tables, including legacy unscoped tables, live in separate stores.

    organization_id must come from validated server membership, never a path or
    a caller-supplied workspace_id. Initialization is serialized per process.
    """
    from app.saas.context import organization_directory
    folder = organization_directory(organization_id)
    key = str(folder)
    with _tenant_store_lock:
        if key not in _tenant_stores:
            import app.models  # noqa: F401
            if len(_tenant_stores) >= 256:
                raise RuntimeError("本实例组织数量达到容量上限，请联系运营人员")
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            tenant_engine = create_engine(f"sqlite:///{folder / 'content.db'}", pool_pre_ping=True,
                connect_args={"check_same_thread": False, "timeout": 30})
            @event.listens_for(tenant_engine, "connect")
            def _tenant_pragmas(connection, record):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
            Base.metadata.create_all(tenant_engine)
            from app.db.init_db import _ensure_model_usage_columns
            _ensure_model_usage_columns(tenant_engine)
            factory = sessionmaker(autocommit=False, autoflush=False, bind=tenant_engine)
            # Each store gets its own default workspace/knowledge-base identities.
            from app.agent_core.boundaries import workspace_context, get_knowledge_base_or_default
            with factory() as db:
                context = workspace_context(db)
                get_knowledge_base_or_default(db, context)
                from app.services.content_growth_agent import recover_interrupted_agent_runs
                recover_interrupted_agent_runs(db)
            _tenant_stores[key] = (tenant_engine, factory)
        return _tenant_stores[key][1]()


def count_tenant_documents(organization_id: str) -> int:
    from app.models.knowledge_base import KnowledgeDocument
    with tenant_session(organization_id) as db:
        return db.query(KnowledgeDocument).count()


def close_tenant_stores():
    with _tenant_store_lock:
        for tenant_engine, _ in _tenant_stores.values():
            tenant_engine.dispose()
        _tenant_stores.clear()


def SessionLocal(**kwargs):
    from app.saas.context import current_tenant, is_saas_mode
    if is_saas_mode():
        context = current_tenant.get()
        if context is None:
            raise RuntimeError("SaaS 业务操作缺少已验证的组织上下文")
        return tenant_session(context.organization_id)
    return _local_session_factory(**kwargs)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

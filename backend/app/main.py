"""FastAPI 主入口"""
from contextlib import asynccontextmanager, ExitStack
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.core.config import settings
from app.db.init_db import init_database
from app.api.routes import health, topics, drafts, cards, publish_logs, reports, models, sources, agent_runs, predictions, v04
from app.api.routes import knowledge, source_hub, evidence, github_sources, brands, delivery
from app.api.routes import pilot
from app.db.session import SessionLocal
from app.db.runtime_lock import local_database_runtime_lock, saas_data_runtime_lock
from app.core.diagnostics import RequestDiagnosticsMiddleware, RequestErrorsMiddleware, configure_diagnostics_logging

@asynccontextmanager
async def lifespan(app):
    from app.saas.context import is_saas_mode
    saas = is_saas_mode()
    # Port binding happens after lifespan startup. Lock before schema/recovery
    # so another process cannot mark the live owner's tasks as interrupted.
    runtime_lock = (saas_data_runtime_lock(os.getenv("SAAS_DATA_DIR", str(Path(__file__).resolve().parents[2] / ".data" / "saas")))
                    if saas else local_database_runtime_lock(settings.DATABASE_URL))
    with runtime_lock, ExitStack() as cleanup:
        from app.agent_core.vector_store import close_vector_stores
        from app.saas.store import close_control_db
        from app.db.session import close_tenant_stores
        # LIFO: close vectors, tenant databases, then control database. Every
        # cleanup is attempted, including after partial startup, before unlock.
        cleanup.callback(close_control_db)
        cleanup.callback(close_tenant_stores)
        cleanup.callback(close_vector_stores)
        if saas:
            from app.saas.middleware import public_origin
            from app.saas.store import init_control_db
            public_origin()
            from app.saas.routes import organization_limits
            organization_limits()
            init_control_db()
        else:
            init_database()
            from app.services.content_growth_agent import recover_interrupted_agent_runs
            with SessionLocal() as db:
                recover_interrupted_agent_runs(db)
        yield


app = FastAPI(title=settings.APP_NAME, version=__version__, lifespan=lifespan)
configure_diagnostics_logging()
app.add_middleware(RequestErrorsMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Server-Timing"],
)

from app.saas.middleware import SaaSMiddleware
from app.saas import routes as saas_routes, commerce_routes
from app.saas.connections import ConnectionUnavailable
app.add_middleware(SaaSMiddleware)
# Last registered user middleware is outermost: include SaaS's early rejections.
app.add_middleware(RequestDiagnosticsMiddleware)
app.include_router(saas_routes.router)
app.include_router(commerce_routes.router, prefix="/api")

@app.exception_handler(ConnectionUnavailable)
async def connection_error(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})

# 健康检查（两个路径都保留）
app.include_router(health.router)
app.include_router(health.router, prefix="/api")

# 业务路由
app.include_router(topics.router, prefix="/api")
app.include_router(drafts.router, prefix="/api")
app.include_router(cards.router, prefix="/api")
app.include_router(publish_logs.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(agent_runs.router, prefix="/api")
app.include_router(predictions.router, prefix="/api")
app.include_router(v04.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(source_hub.router, prefix="/api")
app.include_router(evidence.router, prefix="/api")
app.include_router(github_sources.router, prefix="/api")
app.include_router(brands.router, prefix="/api")
app.include_router(delivery.router, prefix="/api")
app.include_router(pilot.router, prefix="/api")


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def frontend_index():
        return FileResponse(FRONTEND_DIST / "index.html")

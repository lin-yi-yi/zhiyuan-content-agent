"""FastAPI 主入口"""
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db.init_db import init_database
from app.api.routes import health, topics, drafts, cards, publish_logs, reports, models, sources, agent_runs, predictions, v04
from app.api.routes import knowledge, source_hub, evidence, github_sources, brands, delivery
from app.api.routes import pilot
from app.db.session import SessionLocal

@asynccontextmanager
async def lifespan(app):
    from app.saas.context import is_saas_mode
    if is_saas_mode():
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
    try:
        yield
    finally:
        from app.agent_core.vector_store import close_vector_stores
        close_vector_stores()
        from app.saas.store import close_control_db
        from app.db.session import close_tenant_stores
        close_tenant_stores()
        close_control_db()


app = FastAPI(title=settings.APP_NAME, version="0.10.0", lifespan=lifespan)


@app.middleware("http")
async def request_trace(request: Request, call_next):
    request_id = uuid.uuid4().hex
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Do not return SQL statements, document bodies, credentials or vendor responses.
        logging.getLogger("content_agent").error("request_failed id=%s path=%s", request_id, request.url.path)
        response = JSONResponse(status_code=500, content={"detail": "处理失败，请查看运行日志并重试。", "request_id": request_id})
    response.headers["X-Request-ID"] = request_id
    response.headers["Server-Timing"] = f"app;dur={(time.perf_counter()-start)*1000:.1f}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.saas.middleware import SaaSMiddleware
from app.saas import routes as saas_routes, commerce_routes
from app.saas.connections import ConnectionUnavailable
app.add_middleware(SaaSMiddleware)
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

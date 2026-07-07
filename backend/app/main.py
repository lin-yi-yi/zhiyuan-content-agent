"""FastAPI 主入口"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.init_db import init_database
from app.api.routes import health, topics, drafts, cards, publish_logs, reports, models, sources, agent_runs, predictions, v04

app = FastAPI(title=settings.APP_NAME, version="0.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.on_event("startup")
async def startup():
    init_database()

"""模型管理 API — 查看可用模型、切换、测试连接、调用记录"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.config import settings
from app.llm.router import router as llm_router
from app.llm.tracing import serialize_model_run

router = APIRouter(prefix="/models", tags=["models"])


class ChatRequest(BaseModel):
    provider: str | None = None
    model: str = ""
    system_prompt: str = Field(default="你是一个有用的助手", max_length=8000)
    user_prompt: str = Field(default="", max_length=16000)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=2048, ge=1, le=4096)


class ChatResponse(BaseModel):
    content: str
    provider: str
    model: str


@router.get("/providers")
def list_providers():
    """列出所有可用模型供应商及其配置状态"""
    return {"providers": llm_router.list_available_providers()}


@router.get("/task-defaults")
def task_defaults():
    """查看各 Agent 任务默认使用的模型"""
    return {
        "defaults": [
            {"task": "topic_score", "label": "选题评分", "provider": settings.TOPIC_SCORE_PROVIDER, "model": settings.TOPIC_SCORE_MODEL},
            {"task": "draft_generation", "label": "发布包生成", "provider": settings.DRAFT_GENERATION_PROVIDER, "model": settings.DRAFT_GENERATION_MODEL},
            {"task": "card_generation", "label": "卡片生成", "provider": settings.CARD_GENERATION_PROVIDER, "model": settings.CARD_GENERATION_MODEL},
            {"task": "compliance_check", "label": "合规检查", "provider": settings.COMPLIANCE_CHECK_PROVIDER, "model": settings.COMPLIANCE_CHECK_MODEL},
        ],
    }


@router.post("/test/{provider}")
def test_connection(provider: str):
    """测试指定供应商的连接"""
    result = llm_router.test_connection(provider)
    if not result["ok"]:
        raise HTTPException(503, f"{provider} 连接失败: {result.get('error', '')}")
    return result


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """调用指定模型进行对话（同步）"""
    if not req.user_prompt:
        raise HTTPException(400, "user_prompt 不能为空")
    client = llm_router.get_client(provider=req.provider, model=req.model or None)
    content = client.chat(req.system_prompt, req.user_prompt, req.temperature, req.max_tokens)
    return ChatResponse(content=content, provider=getattr(client, "provider", req.provider or "local"), model=req.model or client.model)


@router.get("/runs")
def list_runs(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    """Return usage metadata only, including for historical rows containing text."""
    from app.models.model_run import ModelRun
    runs = db.query(ModelRun).order_by(ModelRun.created_at.desc()).limit(limit).all()
    return {
        "runs": [serialize_model_run(row) for row in runs],
    }

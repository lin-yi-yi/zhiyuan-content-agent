"""LLM Router — 多模型管理和切换"""
import os
from typing import Optional

from app.core.config import settings
from app.llm.base import BaseLLMClient
from app.llm.local import LocalRuleBasedClient
from app.llm.openai_compatible import OpenAICompatibleClient


class ModelRouter:
    PROVIDERS = {
        "local": {
            "api_key": "local",
            "base_url": "local://rule-based",
            "model": "local-rule-based-v0",
        },
        "deepseek": {
            "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
            "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        },
        "qwen": {
            "api_key": os.getenv("QWEN_API_KEY", ""),
            "base_url": os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "model": os.getenv("QWEN_MODEL", "qwen-plus"),
        },
        "doubao": {
            "api_key": os.getenv("DOUBAO_API_KEY", ""),
            "base_url": os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            "model": os.getenv("DOUBAO_MODEL", "doubao-1-5-pro-32k"),
        },
        "kimi": {
            "api_key": os.getenv("KIMI_API_KEY", ""),
            "base_url": os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
            "model": os.getenv("KIMI_MODEL", "moonshot-v1-8k"),
        },
    }

    def __init__(self):
        self._clients: dict[str, BaseLLMClient] = {}

    def get_client(self, provider: Optional[str] = None, model: Optional[str] = None) -> BaseLLMClient:
        from app.saas.context import is_saas_mode
        if is_saas_mode():
            from app.saas.connections import enforce_provider, ConnectionUnavailable
            provider = enforce_provider(provider)
            configured_model = self.PROVIDERS[provider]["model"]
            if model and model != configured_model:
                raise ConnectionUnavailable("请使用运营已配置的模型")
            model = configured_model
        provider = provider or settings.DEFAULT_LLM_PROVIDER
        model = model or self.PROVIDERS.get(provider, {}).get("model", "")
        cache_key = f"{provider}:{model}"
        if cache_key in self._clients:
            return self._clients[cache_key]

        if provider not in self.PROVIDERS:
            raise ValueError(f"未知的模型供应商: {provider}。支持: {list(self.PROVIDERS.keys())}")

        cfg = self.PROVIDERS[provider]
        if provider == "local":
            client = LocalRuleBasedClient()
            self._clients[cache_key] = client
            return client

        if not cfg["api_key"]:
            raise ValueError(f"{provider} API Key 未配置，请在 .env 中设置 {provider.upper()}_API_KEY")

        client = OpenAICompatibleClient(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            model=model or cfg["model"],
            provider=provider,
        )
        self._clients[cache_key] = client
        return client

    def get_default_client(self) -> BaseLLMClient:
        return self.get_client()

    def get_task_client(self, task_type: str, provider: Optional[str] = None, model: Optional[str] = None) -> BaseLLMClient:
        from app.saas.context import is_saas_mode
        if is_saas_mode():
            return self.get_client(provider, model)
        defaults = {
            "topic_score": (settings.TOPIC_SCORE_PROVIDER, settings.TOPIC_SCORE_MODEL),
            "draft_generation": (settings.DRAFT_GENERATION_PROVIDER, settings.DRAFT_GENERATION_MODEL),
            "draft_variant_generation": (settings.DRAFT_GENERATION_PROVIDER, settings.DRAFT_GENERATION_MODEL),
            "card_generation": (settings.CARD_GENERATION_PROVIDER, settings.CARD_GENERATION_MODEL),
            "compliance_check": (settings.COMPLIANCE_CHECK_PROVIDER, settings.COMPLIANCE_CHECK_MODEL),
        }
        default_provider, default_model = defaults.get(
            task_type,
            (settings.DEFAULT_LLM_PROVIDER, settings.DEFAULT_LLM_MODEL),
        )
        # A selected provider must use its own default model, not another vendor's.
        selected = provider or default_provider
        selected_model = model or (default_model if selected == default_provider else None)
        return self.get_client(selected, selected_model)

    def list_available_providers(self) -> list[dict]:
        from app.saas.context import current_tenant, is_saas_mode
        if is_saas_mode():
            from app.saas.connections import connection_snapshot
            context = current_tenant.get()
            if context is None:
                return []
            cards = connection_snapshot(context.organization_id)["connections"]
            return [{"provider": c["provider"], "model": self.PROVIDERS[c["provider"]]["model"],
                     "configured": c["configured"], "enabled": c["effective_enabled"],
                     "is_default": c["is_default"], "status": c["status"]}
                    for c in cards if c["kind"] == "model"]
        result = []
        for name, cfg in self.PROVIDERS.items():
            result.append({
                "provider": name,
                "model": cfg["model"],
                "configured": bool(cfg["api_key"]),
                "base_url": cfg["base_url"],
            })
        return result

    def test_connection(self, provider: str) -> dict:
        try:
            client = self.get_client(provider)
            resp = client.chat("你是一个助手", "回复：连接成功", max_tokens=20)
            return {"provider": provider, "ok": True, "response": resp[:50]}
        except Exception as e:
            return {"provider": provider, "ok": False, "error": "连接未通过，请检查接入设置、服务状态和运营日志"}


router = ModelRouter()

"""Organization connection policy; settings never grant upstream authorization."""
import os

from sqlalchemy import select

from app.core.config import settings
from app.saas import store
from app.saas.commerce import atomic_session
from app.saas.commerce_models import OrganizationConnection


CATALOG = {
    "aihot": ("AIHOT", "source", "只读资讯摘要；SaaS 使用须由运营方先确认商业授权。"),
    "github": ("GitHub Releases", "source", "仅读取允许列表中维护者的发布元数据，不读取任意 URL 或执行命令。"),
    "markdown": ("Markdown 文件", "import", "由用户主动上传文档；不自动访问本机文件。"),
    "obsidian": ("Obsidian 文件导入", "import", "导入用户选择的 Markdown 文件，不持续同步或读取整个笔记库。"),
    "local": ("本地演示模型", "model", "确定性演示，用于走通流程；不代表在线大模型能力。"),
    "deepseek": ("DeepSeek", "model", "使用服务运营方配置的模型凭证；配置存在不代表连接实测成功。"),
    "qwen": ("通义千问", "model", "使用服务运营方配置的模型凭证；配置存在不代表连接实测成功。"),
    "doubao": ("豆包", "model", "使用服务运营方配置的模型凭证；配置存在不代表连接实测成功。"),
    "kimi": ("Kimi", "model", "使用服务运营方配置的模型凭证；配置存在不代表连接实测成功。"),
    "feishu": ("飞书", "integration", "规划中，当前没有通讯录、群聊读取或消息发送能力。"),
    "wecom": ("企业微信", "integration", "规划中，当前没有通讯录、群聊读取或消息发送能力。"),
}
MODEL_PROVIDERS = frozenset({"local", "deepseek", "qwen", "doubao", "kimi"})
DEFAULT_ENABLED = frozenset({"markdown", "obsidian", "local"})


class ConnectionUnavailable(ValueError):
    pass


def _organization(organization_id):
    if organization_id:
        return organization_id
    from app.saas.context import current_tenant
    context = current_tenant.get()
    if context is None:
        raise ConnectionUnavailable("需要有效的组织会话。")
    return context.organization_id


def _availability(provider):
    if provider in {"feishu", "wecom"}:
        return False, False, "planned"
    if provider == "aihot":
        authorized = os.getenv("AIHOT_COMMERCIAL_AUTHORIZED", "false").strip().lower() in {"true", "1", "yes", "on"}
        if not authorized:
            return False, False, "authorization_required"
        if not settings.AIHOT_ENABLED:
            return False, True, "operator_disabled"
    if provider == "github" and not settings.GITHUB_ENABLED:
        return False, True, "operator_disabled"
    if provider in MODEL_PROVIDERS - {"local"}:
        # Read the exact configuration used by the existing client, no key copy
        # in responses, no network probe and no credentials accepted from users.
        from app.llm.router import ModelRouter
        configured = bool(ModelRouter.PROVIDERS.get(provider, {}).get("api_key"))
        return configured, configured, "disabled" if configured else "not_configured"
    return True, True, "disabled"


def _default(rows):
    return next((row.provider for row in rows.values() if row.is_default and row.enabled
                 and row.provider in MODEL_PROVIDERS), "local")


def _view(provider, rows, default, *, owner=False):
    if provider not in CATALOG:
        raise ConnectionUnavailable("不支持的连接类型。")
    name, kind, description = CATALOG[provider]
    row = rows.get(provider)
    enabled = row.enabled if row else provider in DEFAULT_ENABLED
    available, configured, blocked = _availability(provider)
    supported = kind != "integration"
    status = "enabled" if available and enabled else "disabled" if available else blocked
    return {"provider": provider, "name": name, "kind": kind, "supported": supported,
            "enabled": enabled, "effective_enabled": enabled and available, "configured": configured,
            "status": status, "can_toggle": bool(owner and supported and provider != "local" and (available or enabled)),
            "is_default": provider == default, "credential_status": ("configured" if configured else "not_configured")
            if provider in MODEL_PROVIDERS - {"local"} else "not_required",
            "credential_source": "operator_environment" if provider in MODEL_PROVIDERS - {"local"} else None,
            "description": description}


def connection_snapshot(organization_id, *, owner=False):
    with store.session_factory() as db:
        rows = {row.provider: row for row in db.scalars(select(OrganizationConnection).where(
            OrganizationConnection.organization_id == organization_id)).all()}
    default = _default(rows)
    return {"connections": [_view(provider, rows, default, owner=owner) for provider in CATALOG],
            "default_model_provider": default}


def connection_state(provider, *, organization_id=None):
    organization_id = _organization(organization_id)
    if provider not in CATALOG:
        raise ConnectionUnavailable("不支持的连接类型。")
    return next(item for item in connection_snapshot(organization_id)["connections"] if item["provider"] == provider)


def require_connection(provider, *, organization_id=None):
    state = connection_state(provider, organization_id=organization_id)
    if not state["effective_enabled"]:
        messages = {"authorization_required": "运营方尚未确认该资讯源的 SaaS 商业授权。",
                    "operator_disabled": "运营方尚未启用该连接。", "not_configured": "运营方尚未配置该模型凭证。",
                    "planned": "该集成仍在规划中。", "disabled": "该组织尚未启用该连接。"}
        raise ConnectionUnavailable(messages.get(state["status"], "该连接当前不可用。"))
    return state


def get_default_model_provider(organization_id=None):
    return connection_snapshot(_organization(organization_id))["default_model_provider"]


def enforce_provider(provider=None, *, organization_id=None):
    organization_id = _organization(organization_id)
    provider = provider or get_default_model_provider(organization_id)
    if provider not in MODEL_PROVIDERS:
        raise ConnectionUnavailable("不支持的模型供应商。")
    require_connection(provider, organization_id=organization_id)
    return provider


def update_connection(organization_id, provider, *, enabled=None, is_default=None):
    if provider not in CATALOG or CATALOG[provider][1] == "integration":
        raise ConnectionUnavailable("该连接当前不支持配置。")
    if enabled is None and is_default is None:
        raise ConnectionUnavailable("至少提供一个连接设置。")
    if ((enabled is not None and type(enabled) is not bool)
            or (is_default is not None and type(is_default) is not bool)):
        raise ConnectionUnavailable("连接开关必须为布尔值。")
    if is_default is not None and provider not in MODEL_PROVIDERS:
        raise ConnectionUnavailable("只有模型连接可以设为默认。")
    if provider == "local" and enabled is False:
        raise ConnectionUnavailable("本地演示模型保留为可选项，不能禁用。")
    with atomic_session() as db:
        rows = {row.provider: row for row in db.scalars(select(OrganizationConnection).where(
            OrganizationConnection.organization_id == organization_id)).all()}
        row = rows.get(provider)
        target_enabled = enabled if enabled is not None else (row.enabled if row else provider in DEFAULT_ENABLED)
        if target_enabled or is_default:
            available, _, reason = _availability(provider)
            if not available:
                messages = {"authorization_required": "运营方尚未确认 AIHOT 的 SaaS 商业授权，组织开关不能代替授权。",
                            "operator_disabled": "运营方尚未启用该连接。", "not_configured": "运营方尚未配置该模型凭证。"}
                raise ConnectionUnavailable(messages.get(reason, "该连接当前不可用。"))
        if is_default and not target_enabled:
            raise ConnectionUnavailable("请先启用模型，再设为默认。")
        if row is None:
            row = OrganizationConnection(organization_id=organization_id, provider=provider,
                                         enabled=target_enabled, is_default=False)
            db.add(row)
        row.enabled = target_enabled
        if is_default:
            for other in rows.values():
                other.is_default = False
        if is_default is not None:
            row.is_default = is_default
        if not target_enabled:
            row.is_default = False
    return connection_snapshot(organization_id, owner=True)

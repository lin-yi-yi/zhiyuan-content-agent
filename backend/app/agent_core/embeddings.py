"""Explicit retrieval configuration and genuine semantic embedding providers.

Lexical mode never creates vectors. Semantic/hybrid failures are surfaced, never replaced
with hashes or silently downgraded. Model downloads happen only when used.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import importlib.util
import math
import os
from pathlib import Path
from threading import RLock
from urllib.parse import urlparse

import httpx


class RetrievalError(ValueError):
    """Safe, user-visible configuration or retrieval error."""


@dataclass(frozen=True)
class RetrievalConfig:
    mode: str
    provider: str
    model: str
    base_url: str
    api_key: str
    cache_dir: str
    vector_path: str
    semantic_threshold: float

    @property
    def fingerprint(self) -> str:
        identity = f"{self.provider}:{self.model}:{self.base_url if self.provider == 'openai' else ''}"
        return hashlib.sha256(identity.encode()).hexdigest()[:16]


def retrieval_config(mode_override: str | None = None) -> RetrievalConfig:
    from app.saas.context import current_tenant, is_saas_mode, organization_directory
    mode = (mode_override or os.getenv("RAG_RETRIEVAL_MODE", "lexical")).strip().lower()
    provider = os.getenv("RAG_EMBEDDING_PROVIDER", "fastembed").strip().lower()
    default_model = "BAAI/bge-small-zh-v1.5" if provider == "fastembed" else ""
    model = os.getenv("RAG_EMBEDDING_MODEL", default_model).strip()
    base_url = os.getenv("RAG_EMBEDDING_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("RAG_EMBEDDING_API_KEY", "").strip()
    root = Path(__file__).resolve().parents[3]
    try:
        threshold = float(os.getenv("RAG_SEMANTIC_MIN_SCORE", "0.55"))
    except ValueError as exc:
        raise RetrievalError("RAG_SEMANTIC_MIN_SCORE 必须是 0 到 1 之间的数字") from exc
    if mode not in {"lexical", "semantic", "hybrid"}:
        raise RetrievalError("RAG_RETRIEVAL_MODE 只能为 lexical、semantic 或 hybrid")
    if not 0 <= threshold <= 1:
        raise RetrievalError("RAG_SEMANTIC_MIN_SCORE 必须在 0 到 1 之间")
    if mode in {"semantic", "hybrid"}:
        if provider not in {"fastembed", "openai"} or not model:
            raise RetrievalError("语义检索需要有效的 embedding provider 和 model")
        if provider == "openai":
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
                raise RetrievalError("请配置合法的 RAG_EMBEDDING_BASE_URL（例如服务的 /v1 地址）")
            if not api_key:
                raise RetrievalError("请配置 RAG_EMBEDDING_API_KEY；本地兼容服务可设置占位值")
    vector_path = os.getenv("RAG_VECTOR_PATH", str(root / "data" / "qdrant"))
    if is_saas_mode():
        context = current_tenant.get()
        if context is None:
            raise RetrievalError("语义检索缺少已验证的组织上下文")
        vector_path = str(organization_directory(context.organization_id) / "vectors")
    return RetrievalConfig(
        mode, provider, model, base_url, api_key,
        os.getenv("RAG_EMBEDDING_CACHE_DIR") or os.getenv("FASTEMBED_CACHE_PATH")
        or os.getenv("RAG_EMBEDDING_CACHE", str(root / "data" / "embedding-cache")),
        vector_path, threshold,
    )


def retrieval_status(mode_override: str | None = None) -> dict:
    """Cheap configuration inspection; does not claim a successful runtime probe."""
    try:
        config = retrieval_config(mode_override)
    except RetrievalError as exc:
        return {"mode": "invalid", "configured": False, "semantic_enabled": False,
                "runtime_verified": False, "error": str(exc), "limitations": [str(exc)]}
    semantic = config.mode in {"semantic", "hybrid"}
    missing = []
    if semantic:
        for package in (["qdrant_client", "fastembed"] if config.provider == "fastembed" else ["qdrant_client"]):
            if importlib.util.find_spec(package) is None:
                missing.append(package)
    return {
        "mode": config.mode, "configured": not missing, "semantic_enabled": semantic,
        "embedding_provider": config.provider if semantic else None,
        "embedding_model": config.model if semantic else None,
        "vector_store": "qdrant_local" if semantic else None,
        "index_fingerprint": config.fingerprint if semantic else "lexical-v2",
        "runtime_verified": False, "missing_dependencies": missing,
        "semantic_min_score": config.semantic_threshold if semantic else None,
        "strategy": {"semantic": "semantic_cosine", "lexical": "lexical_overlap", "hybrid": "hybrid_bm25_rrf"}[config.mode],
        "available_modes": ["lexical", "semantic", "hybrid"],
        "hybrid": {"fusion": "reciprocal_rank_fusion", "rrf_k": 60, "bm25_k1": 1.2, "bm25_b": 0.75,
                   "lexical_min_overlap": 0.18,
                   "score_usage": "RRF仅用于排序；证据按余弦或词项覆盖门槛独立判定。"} if config.mode == "hybrid" else None,
        "limitations": [
            "配置检查不等于模型已下载或语义检索已验证。",
            "单进程本地 Qdrant；多进程部署需改用独立向量服务。",
            "阈值需用自己的标注问题校准，分数不是答案正确概率。",
        ] + (["BM25在当前合格知识块上计算；中文采用字符二/三元组，尚未引入学习式重排。"] if config.mode == "hybrid" else [])
        if semantic else ["当前为明确的词项检索演示，不使用语义模型或向量数据库。"],
    }


_model_lock = RLock()


@lru_cache(maxsize=2)
def _fastembed_model(model: str, cache_dir: str):
    try:
        from fastembed import TextEmbedding
        return TextEmbedding(model_name=model, cache_dir=cache_dir, threads=2)
    except Exception as exc:
        raise RetrievalError("语义模型加载失败：检查 fastembed 安装、模型名称、缓存和下载网络；未降级为词项检索") from exc


def _validate_vectors(values, count: int) -> list[list[float]]:
    try:
        vectors = [[float(item) for item in vector] for vector in values]
        dims = {len(vector) for vector in vectors}
        if len(vectors) != count or len(dims) != 1 or not vectors or not vectors[0]:
            raise ValueError("count or dimension")
        if any(not math.isfinite(value) for vector in vectors for value in vector):
            raise ValueError("non-finite value")
        if any(sum(value * value for value in vector) == 0 for vector in vectors):
            raise ValueError("zero vector")
        return vectors
    except (TypeError, ValueError, OverflowError) as exc:
        raise RetrievalError("Embedding 服务返回了无效的向量数量、维度或数值") from exc


def embed_texts(texts: list[str], config: RetrievalConfig, *, query: bool = False) -> list[list[float]]:
    if config.mode not in {"semantic", "hybrid"}:
        raise RetrievalError("词项模式不生成 embedding；请显式启用语义模式")
    if not texts:
        return []
    if config.provider == "fastembed":
        with _model_lock:
            model = _fastembed_model(config.model, config.cache_dir)
            try:
                values = list(model.query_embed(texts) if query else model.passage_embed(texts, batch_size=16))
            except Exception as exc:
                raise RetrievalError("语义模型推理失败；未自动降级，请检查模型运行环境") from exc
        return _validate_vectors(values, len(texts))
    try:
        with httpx.Client(timeout=45, follow_redirects=False) as client:
            response = client.post(
                f"{config.base_url}/embeddings", headers={"Authorization": f"Bearer {config.api_key}"},
                json={"model": config.model, "input": texts, "encoding_format": "float"},
            )
            response.raise_for_status()
            data = response.json()["data"]
            if sorted(item["index"] for item in data) != list(range(len(texts))):
                raise ValueError("invalid embedding indexes")
            vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
    except Exception as exc:
        # Remote errors may contain credentials or submitted document text.
        raise RetrievalError("Embedding API 调用失败：检查地址、凭证、模型及服务状态；未自动降级") from exc
    return _validate_vectors(vectors, len(texts))

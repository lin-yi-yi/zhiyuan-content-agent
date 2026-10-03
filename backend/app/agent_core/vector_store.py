"""Single-process persistent Qdrant adapter with mandatory scope filtering."""
from threading import RLock

from app.agent_core.embeddings import RetrievalConfig, RetrievalError


_lock = RLock()
_clients: dict = {}


def _client(path: str):
    if path in _clients:
        return _clients[path]
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(path=path, force_disable_check_same_thread=True)
        _clients[path] = client
        return client
    except Exception as exc:
        raise RetrievalError("Qdrant 本地存储无法打开：检查依赖、目录权限及是否被另一进程占用") from exc


def _models():
    try:
        from qdrant_client import models
        return models
    except ImportError as exc:
        raise RetrievalError("请安装 qdrant-client 后使用语义索引") from exc


def collection_name(config: RetrievalConfig, dimension: int) -> str:
    return f"knowledge_{config.fingerprint}_{dimension}"


def upsert_chunks(config: RetrievalConfig, chunks: list, vectors: list[list[float]], content_hash: str) -> str:
    models = _models()
    collection = collection_name(config, len(vectors[0]))
    with _lock:
        client = _client(config.vector_path)
        try:
            if not client.collection_exists(collection):
                client.create_collection(collection, vectors_config=models.VectorParams(size=len(vectors[0]), distance=models.Distance.COSINE))
            client.upsert(collection, points=[models.PointStruct(
                id=chunk.id, vector=vector,
                payload={"workspace_id": chunk.workspace_id, "knowledge_base_id": chunk.knowledge_base_id,
                         "document_id": chunk.document_id, "content_hash": content_hash,
                         "chunk_hash": chunk.embedding_hash},
            ) for chunk, vector in zip(chunks, vectors, strict=True)], wait=True)
        except Exception as exc:
            raise RetrievalError("向量索引写入失败；请检查 Qdrant 本地存储后重新索引") from exc
    return collection


def search_vectors(config: RetrievalConfig, vector: list[float], workspace_id: int, knowledge_base_id: int,
                   limit: int, document_ids: list[int] | None = None) -> list[dict]:
    # Filter eligibility before ranking/limiting: revoked or expired documents
    # must not occupy every candidate slot and hide still-current evidence.
    if document_ids == []:
        return []
    models = _models()
    collection = collection_name(config, len(vector))
    conditions = [
        models.FieldCondition(key="workspace_id", match=models.MatchValue(value=workspace_id)),
        models.FieldCondition(key="knowledge_base_id", match=models.MatchValue(value=knowledge_base_id)),
    ]
    if document_ids is not None:
        conditions.append(models.FieldCondition(key="document_id", match=models.MatchAny(any=document_ids)))
    with _lock:
        client = _client(config.vector_path)
        try:
            if not client.collection_exists(collection):
                raise RetrievalError("当前模型对应的向量索引不存在，请重新索引知识库")
            result = client.query_points(
                collection_name=collection, query=vector, limit=limit, with_payload=True,
                query_filter=models.Filter(must=conditions),
            )
            return [{"id": int(point.id), "score": float(point.score), "payload": point.payload or {}} for point in result.points]
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError("向量检索失败；未自动降级为词项检索") from exc


def delete_points(config: RetrievalConfig, collection: str, ids: list[int]) -> None:
    if not ids:
        return
    models = _models()
    with _lock:
        client = _client(config.vector_path)
        try:
            if client.collection_exists(collection):
                client.delete(collection, points_selector=models.PointIdsList(points=ids), wait=True)
        except Exception as exc:
            raise RetrievalError("旧向量清理失败；数据库已生效，旧向量不会作为有效证据返回") from exc


def close_vector_stores() -> None:
    """Test/CLI shutdown helper; application should use one worker in local mode."""
    with _lock:
        for client in _clients.values():
            client.close()
        _clients.clear()

FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.lock.txt backend/requirements.lock.txt
RUN pip install --no-cache-dir -r backend/requirements.lock.txt
COPY backend/ backend/
COPY scripts/ scripts/
COPY docs/ docs/
COPY --from=frontend /web/dist frontend/dist
RUN useradd --create-home appuser && mkdir -p /app/.data && chown -R appuser:appuser /app
USER appuser
ENV PYTHONPATH=/app/backend DATABASE_URL=sqlite:////app/.data/demo.db RAG_RETRIEVAL_MODE=semantic RAG_EMBEDDING_CACHE_DIR=/app/.data/models RAG_VECTOR_PATH=/app/.data/qdrant DEFAULT_LLM_PROVIDER=local DEFAULT_LLM_MODEL=local-rule-based-v0
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]

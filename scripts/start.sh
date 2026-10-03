#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
if [ ! -x .venv/bin/python ] || [ ! -d frontend/node_modules ]; then
  printf '首次使用请先运行 ./scripts/setup.sh\n' >&2
  exit 1
fi
export SAAS_MODE=false
mkdir -p .data
export DATABASE_URL="${DATABASE_URL:-sqlite:///$PROJECT_ROOT/.data/demo.db}"
export RAG_RETRIEVAL_MODE="${RAG_RETRIEVAL_MODE:-semantic}"
export RAG_EMBEDDING_CACHE_DIR="${RAG_EMBEDDING_CACHE_DIR:-$PROJECT_ROOT/.data/models}"
export RAG_VECTOR_PATH="${RAG_VECTOR_PATH:-$PROJECT_ROOT/.data/qdrant}"
export DEFAULT_LLM_PROVIDER="${DEFAULT_LLM_PROVIDER:-local}"
export DEFAULT_LLM_MODEL="${DEFAULT_LLM_MODEL:-local-rule-based-v0}"
# This launcher binds loopback only. Public deployments must review source licensing first.
export AIHOT_ENABLED="${AIHOT_ENABLED:-true}"
export GITHUB_ENABLED="${GITHUB_ENABLED:-true}"
export PYTHONPATH="$PROJECT_ROOT/backend"
(cd frontend && npm run build)
printf '\n知源内容工作台：http://127.0.0.1:%s\n按 Ctrl+C 停止；数据保存在 .data 中。\n' "${PORT:-8765}"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8765}" --workers 1

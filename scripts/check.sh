#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
export PYTHON_DOTENV_DISABLED=1
export SAAS_MODE=false
export DATABASE_URL=sqlite:///:memory:
export RAG_RETRIEVAL_MODE=lexical
export AIHOT_ENABLED=false
export GITHUB_ENABLED=false
export MODEL_PRICING_JSON='[]'
for tracing_namespace in LANGSMITH LANGCHAIN; do
  export "${tracing_namespace}_TRACING=false"
  export "${tracing_namespace}_TRACING_V2=false"
  export "${tracing_namespace}_API_KEY="
done
unset LANGCHAIN_HANDLER
# Ignore online defaults inherited from the operator's shell as well as .env.
for provider in DEEPSEEK QWEN DOUBAO KIMI OPENAI; do
  export "${provider}_API_KEY="
done
for task in DEFAULT_LLM TOPIC_SCORE DRAFT_GENERATION CARD_GENERATION COMPLIANCE_CHECK; do
  export "${task}_PROVIDER=local"
  export "${task}_MODEL=local-rule-based-v0"
done
export PYTHONPATH="$PROJECT_ROOT/backend"
.venv/bin/python -m pytest tests -q
.venv/bin/python -m compileall -q backend/app
(cd frontend && npm run test:request && npm run test:workflow && npm run build)

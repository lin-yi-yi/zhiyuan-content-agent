#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
export PYTHON_DOTENV_DISABLED=1
export SAAS_MODE=false
export DATABASE_URL=sqlite:///:memory:
export RAG_RETRIEVAL_MODE=lexical
export PYTHONPATH="$PROJECT_ROOT/backend"
.venv/bin/python -m pytest tests -q
.venv/bin/python -m compileall -q backend/app
(cd frontend && npm run test:request && npm run test:workflow && npm run build)

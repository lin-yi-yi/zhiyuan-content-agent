#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
if [ ! -x .venv/bin/python ]; then
  if [ -z "${PYTHON_BIN:-}" ] && command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN=python3.12
  fi
  "${PYTHON_BIN:-python3}" -m venv .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info >= (3,12), "锁定依赖需要 Python 3.12 或更新版本；请通过 PYTHON_BIN 指定解释器"'
if command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python -r backend/requirements.lock.txt
else
  .venv/bin/python -m ensurepip --upgrade
  .venv/bin/python -m pip install -r backend/requirements.lock.txt
fi
(cd frontend && npm ci)
printf '\n依赖已准备好。运行 ./scripts/start.sh 启动。\n'

#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
PYTHON_BIN="${SAAS_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  printf '缺少项目 Python 环境，请先运行 ./scripts/setup.sh。\n' >&2
  exit 1
fi
if [ ! -f frontend/dist/index.html ]; then
  printf '缺少前端构建，请先在 frontend 目录运行 npm run build。\n' >&2
  exit 1
fi
export SAAS_PROJECT_ROOT="$PROJECT_ROOT"
exec "$PYTHON_BIN" - <<'PY'
import fcntl
import importlib.util
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

root = Path(os.environ["SAAS_PROJECT_ROOT"])
for module in ("uvicorn", "fastapi", "sqlalchemy", "dotenv"):
    if importlib.util.find_spec(module) is None:
        raise SystemExit("缺少项目依赖，请先运行 ./scripts/setup.sh。")
from dotenv import dotenv_values
env_file = Path(os.getenv("SAAS_ENV_FILE", str(root / ".env.saas"))).expanduser().resolve()
if env_file.exists():
    for name, value in dotenv_values(env_file).items():
        if value is not None:
            os.environ.setdefault(name, value)
os.environ["SAAS_MODE"] = "true"
os.environ["SAAS_ENV_FILE"] = str(env_file)
# The explicit parser above loads only the SaaS file, even with an older app
# configuration that would otherwise implicitly discover the demo .env.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
data = Path(os.getenv("SAAS_DATA_DIR", str(root / ".data" / "saas"))).expanduser().resolve()
data.mkdir(parents=True, exist_ok=True, mode=0o700)
os.environ["SAAS_DATA_DIR"] = str(data)
port = os.getenv("SAAS_PORT", "8766")
if not port.isdecimal() or not 1024 <= int(port) <= 65535:
    raise SystemExit("SAAS_PORT 必须是 1024 至 65535 的端口。")
origin = os.getenv("SAAS_PUBLIC_ORIGIN", f"http://127.0.0.1:{port}").rstrip("/")
parsed = urlsplit(origin)
if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path or not parsed.hostname:
    raise SystemExit("SAAS_PUBLIC_ORIGIN 必须是无路径、无凭证的源地址。")
local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
if parsed.scheme != "https" and not local:
    raise SystemExit("公开访问必须使用 HTTPS；HTTP 仅允许本机地址。")
secure = os.getenv("SAAS_SECURE_COOKIE", "false" if local else "true").lower()
if secure not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
    raise SystemExit("SAAS_SECURE_COOKIE 必须是布尔值。")
if not local and secure in {"false", "0", "no", "off"}:
    raise SystemExit("公开 HTTPS 环境必须启用安全 Cookie。")
os.environ["SAAS_PUBLIC_ORIGIN"], os.environ["SAAS_SECURE_COOKIE"] = origin, secure
os.environ["DATABASE_URL"] = f"sqlite:///{data / 'legacy-disabled.db'}"
os.environ.setdefault("RAG_EMBEDDING_CACHE_DIR", str(root / ".data" / "models"))
os.environ.setdefault("RAG_RETRIEVAL_MODE", "semantic")
os.environ.setdefault("AIHOT_ENABLED", "false")
os.environ.setdefault("AIHOT_COMMERCIAL_AUTHORIZED", "false")
os.environ.setdefault("GITHUB_ENABLED", "true")
os.environ["PYTHONPATH"] = str(root / "backend")
descriptor = os.open(data / ".service.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
try:
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("该 SaaS 数据目录已有服务或备份任务运行。") from None
os.set_inheritable(descriptor, True)
print(f"知源 SaaS：{origin}\n本机监听 http://127.0.0.1:{port}；单 worker；数据目录 {data}\n按 Ctrl+C 停止。", flush=True)
os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port, "--workers", "1"])
PY

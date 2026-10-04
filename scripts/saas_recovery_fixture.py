#!/usr/bin/env python3
"""Isolated, synthetic SaaS recovery service; never a semantic-quality benchmark.

The normal app owns authentication, quotas, SQLite, Qdrant and the service lock.
Only this process substitutes explicitly named three-dimensional embeddings.
Importing this file uses the standard library and does not open application data.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import sys
from threading import Lock


ROOT = Path(__file__).resolve().parents[1]
OWNER_FILE = ".recovery-owner"
OWNER_FORMAT = "zhiyuan-synthetic-recovery-root-v1"
SYNTHETIC_PROVIDER = "synthetic"
SYNTHETIC_MODEL = "zhiyuan-recovery-synthetic-3d-v1"
SYNTHETIC_DIMENSION = 3
TOKEN_PATTERN = re.compile(r"[a-f0-9]{32}\Z")


class FixtureError(ValueError):
    """Fixed messages intentionally omit paths, credentials and business content."""


def _token_digest(token: str) -> str:
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        raise FixtureError("Fixture token must be 32 lowercase hexadecimal characters.")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _root_path(root: str | Path) -> Path:
    # Resolve the parent (including platform aliases such as macOS /var), but
    # never follow a symlink at the explicitly selected fixture root itself.
    path = Path(root).expanduser().absolute()
    if path.is_symlink():
        raise FixtureError("Fixture root must be a real directory.")
    path = path.parent.resolve() / path.name
    if path.exists() and not path.is_dir():
        raise FixtureError("Fixture root must be a real directory.")
    return path


def require_fixture_root(root: str | Path, token: str) -> Path:
    """Verify existing ownership without creating directories or claiming data."""
    digest = _token_digest(token)
    path = _root_path(root)
    marker = path / OWNER_FILE
    try:
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024:
            raise FixtureError("Fixture ownership marker is invalid.")
        descriptor = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024:
                raise FixtureError("Fixture ownership marker is invalid.")
            data = json.load(stream)
        expected = {"format": OWNER_FORMAT, "token_sha256": digest}
        if (not isinstance(data, dict) or set(data) != set(expected)
                or data.get("format") != OWNER_FORMAT
                or not isinstance(data.get("token_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", data["token_sha256"])
                or not hmac.compare_digest(data["token_sha256"], digest)):
            raise FixtureError("Fixture ownership verification failed.")
    except (OSError, ValueError, UnicodeError) as exc:
        raise FixtureError("Fixture ownership verification failed.") from exc
    return path


def initialize_fixture_root(root: str | Path, token: str) -> Path:
    """Claim only a new/empty directory, or verify an already owned root."""
    digest = _token_digest(token)
    path = _root_path(root)
    if path.exists() and any(path.iterdir()):
        return require_fixture_root(path, token)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    # O_EXCL prevents replacing an ownership marker created by another caller.
    try:
        descriptor = os.open(path / OWNER_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"format": OWNER_FORMAT, "token_sha256": digest}, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise FixtureError("Fixture root initialization failed.") from exc
    return require_fixture_root(path, token)


def prepare_fixture_store(root: str | Path, store_name: str, token: str) -> Path:
    """Only the two stores under the separately owned parent can be opened."""
    path = require_fixture_root(root, token)
    if store_name not in {"source", "restored"}:
        raise FixtureError("Fixture store must be source or restored.")
    store = path / store_name
    if store.is_symlink() or (store.exists() and not store.is_dir()):
        raise FixtureError("Fixture store must be a real directory.")
    if store.exists():
        # A restored archive is authorized by its parent, but must not redirect
        # SQLite/Qdrant into other data through links or special files.
        for directory, dirs, files in os.walk(store, followlinks=False):
            for name in dirs + files:
                info = (Path(directory) / name).lstat()
                if (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                        or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                    raise FixtureError("Fixture store contains an unsafe filesystem entry.")
    store.mkdir(mode=0o700, exist_ok=True)
    return store


def configure_fixture_environment(store: Path, port: int) -> None:
    """Must run before any app/library import that can read credentials or .env."""
    for name in list(os.environ):
        upper = name.upper()
        if (any(part in upper for part in ("KEY", "TOKEN", "PASSWORD", "SECRET"))
                or upper.startswith(("LANGCHAIN_", "LANGSMITH_", "OTEL_", "SENTRY_", "WANDB_", "COMET_", "TRACELOOP_"))
                or upper in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}):
            os.environ.pop(name, None)
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1", "SAAS_MODE": "true", "APP_ENV": "local",
        "DIAGNOSTIC_LOG_DIR": "",
        "APP_NAME": "知源合成恢复夹具",
        "SAAS_DATA_DIR": str(store), "DATABASE_URL": "sqlite:///:memory:",
        "SAAS_ENV_FILE": str(store / "never-load.env"),
        "SAAS_PUBLIC_ORIGIN": f"http://127.0.0.1:{port}", "SAAS_SECURE_COOKIE": "false",
        "SAAS_ALLOW_REGISTRATION": "true", "SAAS_MAX_ORGANIZATIONS": "10",
        "SAAS_MAX_OWNED_ORGANIZATIONS": "3", "BACKEND_CORS_ORIGINS": f"http://127.0.0.1:{port}",
        "AIHOT_ENABLED": "false", "AIHOT_COMMERCIAL_AUTHORIZED": "false", "GITHUB_ENABLED": "false",
        "MODEL_PRICING_JSON": "[]", "RAG_RETRIEVAL_MODE": "semantic",
        # The original config parser validates scope before our fixture-only
        # adapter replaces the provider with synthetic. FastEmbed is never used.
        "RAG_EMBEDDING_PROVIDER": "fastembed", "RAG_EMBEDDING_MODEL": SYNTHETIC_MODEL,
        "RAG_EMBEDDING_BASE_URL": "", "RAG_EMBEDDING_API_KEY": "",
        "RAG_SEMANTIC_MIN_SCORE": "0.55", "RAG_VECTOR_PATH": str(store / "unused-vectors"),
        "RAG_EMBEDDING_CACHE_DIR": str(store / "unused-model-cache"),
        "FASTEMBED_CACHE_PATH": str(store / "unused-model-cache"),
        "HF_HOME": str(store / "unused-model-cache"), "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
        "LANGCHAIN_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false", "LANGSMITH_TRACING_V2": "false", "OTEL_SDK_DISABLED": "true",
    })
    for provider in ("DEEPSEEK", "QWEN", "DOUBAO", "KIMI", "OPENAI"):
        os.environ[f"{provider}_API_KEY"] = ""
        os.environ[f"{provider}_BASE_URL"] = ""
    for task in ("DEFAULT_LLM", "TOPIC_SCORE", "DRAFT_GENERATION", "CARD_GENERATION", "COMPLIANCE_CHECK"):
        os.environ[f"{task}_PROVIDER"] = "local"
        os.environ[f"{task}_MODEL"] = "local-rule-based-v0"


@dataclass
class IsolationState:
    outbound_connection_attempts: int = 0
    embedding_calls: int = 0
    embedded_text_count: int = 0
    lock: Lock = field(default_factory=Lock)

    def deny(self, *_args, **_kwargs):
        with self.lock:
            self.outbound_connection_attempts += 1
        raise PermissionError("Recovery fixture denies outgoing network connections.")


@contextmanager
def deny_outbound_connections(state: IsolationState):
    """Process-local Python socket guard; inbound accept/reply remains available.

    Not an OS firewall: the Docker orchestrator also uses --network none.
    No destination addresses or request contents are retained in counters.
    """
    originals = (socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto,
                 socket.create_connection, socket.getaddrinfo)

    def guarded_dns(host, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
        except (ValueError, TypeError):
            return state.deny()
        return originals[4](host, *args, **kwargs)

    socket.socket.connect = state.deny
    socket.socket.connect_ex = state.deny
    socket.socket.sendto = state.deny
    socket.create_connection = state.deny
    socket.getaddrinfo = guarded_dns
    try:
        yield
    finally:
        (socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto,
         socket.create_connection, socket.getaddrinfo) = originals


def install_synthetic_embeddings(state: IsolationState) -> None:
    """Install before app.main imports its RAG service; no production fallback."""
    from app.agent_core import embeddings
    original_config = embeddings.retrieval_config

    def fixture_config(mode_override=None):
        config = original_config(mode_override)  # Real tenant/scope validation.
        return replace(config, provider=SYNTHETIC_PROVIDER, model=SYNTHETIC_MODEL, base_url="", api_key="")

    def fixture_embed(texts, config, *, query=False):
        if (config.mode not in {"semantic", "hybrid"} or config.provider != SYNTHETIC_PROVIDER
                or config.model != SYNTHETIC_MODEL):
            raise embeddings.RetrievalError("Recovery fixture requires its explicit synthetic embedding configuration.")
        with state.lock:
            state.embedding_calls += 1
            state.embedded_text_count += len(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]

    embeddings.retrieval_config = fixture_config
    embeddings.embed_texts = fixture_embed


def isolation_snapshot(store: Path, state: IsolationState) -> dict:
    """Read only counts and opaque organization/path identifiers, never payloads."""
    from app.agent_core import vector_store
    with sqlite3.connect((store / "control.db").as_uri() + "?mode=ro", uri=True) as db:
        organizations = [row[0] for row in db.execute("SELECT id FROM saas_organizations ORDER BY id")]
    external = local = 0
    for organization in organizations:
        if not TOKEN_PATTERN.fullmatch(organization):
            raise FixtureError("Fixture organization identifier is invalid.")
        database = store / "tenants" / organization / "content.db"
        if database.is_file():
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                for provider, count in db.execute("SELECT provider, COUNT(*) FROM model_runs GROUP BY provider"):
                    if provider == "local":
                        local += count
                    else:
                        external += count
    paths = []
    with vector_store._lock:
        for path in sorted(vector_store._clients):
            relative = Path(path).resolve().relative_to(store)
            if (len(relative.parts) != 3 or relative.parts[0] != "tenants"
                    or relative.parts[2] != "vectors" or relative.parts[1] not in organizations):
                raise FixtureError("Fixture vector store escaped its organization scope.")
            paths.append({"organization_id": relative.parts[1], "relative_path": relative.as_posix(), "opened": True})
    with state.lock:
        counters = {"outbound_connection_attempts": state.outbound_connection_attempts,
                    "embedding_calls": state.embedding_calls, "embedded_text_count": state.embedded_text_count}
    return {"fixture_mode": "synthetic_recovery", "health_mode": "saas", "process_uid": os.getuid(),
            "embedding_provider": SYNTHETIC_PROVIDER, "embedding_model": SYNTHETIC_MODEL,
            "embedding_dimension": SYNTHETIC_DIMENSION, "generation_provider": "local",
            "tenant_count": len(organizations), "total_external_calls": external, "local_model_run_count": local,
            "external_call_count_basis": "persisted_nonlocal_model_runs",
            "outbound_guard": "python_socket_connect_dns_and_sendto", "semantic_quality_evaluated": False,
            "dotenv_disabled": os.getenv("PYTHON_DOTENV_DISABLED") == "1", "pricing_configured": False,
            "vector_stores": paths, **counters}


def create_fixture_app(store: Path, token: str, port: int, state: IsolationState):
    if any(name == "app" or name.startswith("app.") for name in sys.modules):
        raise FixtureError("Recovery fixture must run in a fresh process before application imports.")
    configure_fixture_environment(store, port)
    sys.path.insert(0, str(ROOT / "backend"))
    install_synthetic_embeddings(state)
    from fastapi import HTTPException, Request
    from app.main import app

    # Avoid a forward-reference annotation resolving Request only in globals.
    def isolation(request):
        supplied_token = request.headers.get("X-Recovery-Fixture-Token", "")
        if (request.client is None or request.client.host not in {"127.0.0.1", "::1"}
                or not TOKEN_PATTERN.fullmatch(supplied_token) or not hmac.compare_digest(supplied_token, token)):
            raise HTTPException(404, "Not found")
        try:
            return isolation_snapshot(store, state)
        except (OSError, ValueError, sqlite3.Error):
            raise HTTPException(503, "Recovery isolation probe unavailable") from None

    isolation.__annotations__["request"] = Request
    app.add_api_route("/__recovery__/isolation", isolation, methods=["GET"], include_in_schema=False)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--store-name", choices=("source", "restored"), default="source")
    parser.add_argument("--fixture-token", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--initialize-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.port <= 65535:
            raise FixtureError("Fixture port must be between 1 and 65535.")
        root = initialize_fixture_root(args.fixture_root, args.fixture_token)
        if args.initialize_only:
            print(json.dumps({"initialized": True, "fixture_mode": "synthetic_recovery"}))
            return 0
        store = prepare_fixture_store(root, args.store_name, args.fixture_token)
        state = IsolationState()
        with deny_outbound_connections(state):
            app = create_fixture_app(store, args.fixture_token, args.port, state)
            import uvicorn
            # The real application's lifespan owns .service.lock throughout
            # database/vector initialization, serving and cleanup.
            uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, proxy_headers=False,
                        access_log=False, log_level="info")
        return 0
    except (OSError, ValueError, RuntimeError):
        print("Recovery fixture failed; check ownership, local permissions and service availability.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

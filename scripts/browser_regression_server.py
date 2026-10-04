"""Serve an empty, disposable application for the real-page Playwright tests.

Only this fixture adds /__e2e__/isolation. It does not pre-create documents,
brands, tasks or approvals: the browser must perform every business mutation.
"""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from evaluate_industrial_faq import configure_offline


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024-65535")
    if not (ROOT / "frontend/dist/index.html").is_file():
        parser.error("Build the isolated frontend with npm run build:e2e first.")

    with TemporaryDirectory(prefix="zhiyuan-browser-e2e-") as folder, ExitStack() as guards:
        # Reuse the industrial FAQ isolation contract before importing the app.
        configure_offline(folder)
        os.environ.update({
            "MODEL_PRICING_JSON": "[]",
            "RAG_EMBEDDING_CACHE_DIR": str(Path(folder) / "models"),
            "BACKEND_CORS_ORIGINS": f"http://127.0.0.1:{args.port}",
        })
        outgoing_attempts = []

        def deny_outgoing(*_args, **_kwargs):
            outgoing_attempts.append(True)
            raise RuntimeError("Browser regression fixture prohibits outgoing connections")

        guards.enter_context(patch.object(socket.socket, "connect", deny_outgoing))
        guards.enter_context(patch.object(socket.socket, "connect_ex", deny_outgoing))
        sys.path.insert(0, str(ROOT / "backend"))
        from fastapi.routing import APIRoute
        from sqlalchemy import func, select
        from app.main import app
        from app.core.config import settings
        from app.agent_core.embeddings import retrieval_config
        from app.db.session import SessionLocal, engine
        from app.models.knowledge_base import KnowledgeDocument
        from app.models.model_run import ModelRun
        from app.llm.router import router

        assert all(not value["api_key"] for key, value in router.PROVIDERS.items() if key != "local")

        def isolation():
            with SessionLocal() as db:
                external_calls = db.scalar(select(func.count()).select_from(ModelRun).where(ModelRun.provider != "local"))
                documents = db.scalar(select(func.count()).select_from(KnowledgeDocument))
            return {
                "fixture": "zhiyuan-browser-regression",
                "temporary_database": settings.DATABASE_URL == f"sqlite:///{Path(folder) / 'industrial-faq.db'}",
                "retrieval_mode": retrieval_config().mode,
                "generation_provider": settings.DEFAULT_LLM_PROVIDER,
                "document_count": documents,
                "external_model_runs": external_calls,
                "outgoing_connection_attempts": len(outgoing_attempts),
            }

        # Production static fallback must not swallow this test-only assertion.
        app.router.routes.insert(0, APIRoute("/__e2e__/isolation", isolation, methods=["GET"]))
        try:
            import uvicorn
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()

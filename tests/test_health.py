"""Health reports the running release; readiness fails closed without DB details."""
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import health
from app.core.config import settings
from app.main import app
from test_saas_isolation import application


@pytest.mark.parametrize("path", ["/health", "/api/health"])
def test_health_matches_running_application_without_database(monkeypatch, path):
    monkeypatch.setenv("SAAS_MODE", "false")
    session = MagicMock(side_effect=OSError("synthetic database unavailable"))
    monkeypatch.setattr(health, "SessionLocal", session)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": settings.APP_NAME,
                               "version": app.version, "scope": "local-single-user"}
    assert app.openapi()["info"]["version"] == response.json()["version"]
    session.assert_not_called()


@pytest.mark.parametrize("path", ["/ready", "/api/ready"])
@pytest.mark.parametrize("stage", ["session", "query"])
def test_unavailable_readiness_is_503_and_hides_internal_details(monkeypatch, path, stage):
    monkeypatch.setenv("SAAS_MODE", "false")
    factory = MagicMock()
    error = OSError("synthetic-private-db-path and credential-marker")
    if stage == "session":
        factory.side_effect = error
    else:
        factory.return_value.__enter__.return_value.execute.side_effect = error
    monkeypatch.setattr(health, "SessionLocal", factory)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get(path)
    finally:
        client.close()
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "unavailable"}
    assert "credential-marker" not in response.text
    assert "synthetic-private-db-path" not in response.text
    if stage == "query":
        factory.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("path", ["/ready", "/api/ready"])
def test_readiness_queries_real_isolated_database(monkeypatch, path):
    monkeypatch.setenv("SAAS_MODE", "false")
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    monkeypatch.setattr(health, "SessionLocal", sessionmaker(bind=engine))
    client = TestClient(app)
    try:
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "database": "connected"}
    finally:
        client.close()
        engine.dispose()


@pytest.mark.parametrize("path", ["/ready", "/api/ready"])
def test_saas_readiness_still_requires_identity(application, monkeypatch, path):
    factory = MagicMock()
    monkeypatch.setattr(health, "SessionLocal", factory)
    assert application.anonymous.get(path).status_code == 401
    factory.assert_not_called()
    live = application.anonymous.get("/api/health")
    assert live.status_code == 200
    assert live.json()["scope"] == "saas-single-host-pilot"

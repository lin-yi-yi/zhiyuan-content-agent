"""Archived learning/job modules must not be exposed by the commercial app."""
import pytest
from test_saas_isolation import application, teams

ARCHIVED_PATHS = ["/api/learning/catalog", "/api/learning/source/frontend-requests", "/api/portfolio"]

@pytest.mark.parametrize("path", ARCHIVED_PATHS)
def test_archived_pages_are_unmounted_for_members(application, teams, path):
    owner, _ = teams
    response = owner.client.get(path)
    assert response.status_code == 404, response.text
    assert "requestGeneration" not in response.text


def test_learning_not_in_production_openapi(application):
    from app.main import app
    paths = app.openapi()["paths"]
    assert not any(path.startswith(("/api/learning", "/api/portfolio")) for path in paths)

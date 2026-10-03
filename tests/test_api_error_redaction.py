"""Legacy API failures must not expose internal exception text."""
import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import drafts, sources, topics
from app.db.session import get_db


@pytest.fixture
def api():
    app = FastAPI()
    for module in (topics, drafts, sources):
        app.include_router(module.router, prefix="/api")
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, db


ENDPOINTS = [
    ("app.api.routes.topics", "generate_custom_topic_ideas", "/api/topics/custom-ideas", {"theme": "demo"}),
    ("app.api.routes.topics", "score_topic", "/api/topics/1/score", {}),
    ("app.api.routes.sources", "generate_source_topic_ideas", "/api/sources/1/topic-ideas", {}),
    ("app.api.routes.drafts", "generate_draft_variant", "/api/drafts/1/generate-variant", {}),
    ("app.services.package_evaluator", "evaluate_draft", "/api/drafts/1/evaluate", {}),
]


@pytest.mark.parametrize("module,name,path,body", ENDPOINTS)
def test_unknown_service_failure_hides_internal_text(api, monkeypatch, module, name, path, body):
    client, _ = api
    monkeypatch.setattr(importlib.import_module(module), name, AsyncMock(
        side_effect=RuntimeError("SQL params: secret-original-document sk-private-key"),
    ))
    response = client.post(path, json=body)
    assert response.status_code == 500
    assert "失败" in response.json()["detail"]
    assert "secret-original-document" not in response.text
    assert "sk-private-key" not in response.text
    assert "SQL params" not in response.text


@pytest.mark.parametrize("stage", ["draft", "cards", "compliance", "commit"])
def test_draft_pipeline_hides_internal_text_at_each_failure_stage(api, monkeypatch, stage):
    client, db = api
    error = RuntimeError("secret-original-document sk-private-key")
    for key, module, name in [
        ("draft", "app.services.draft_generator", "generate_draft"),
        ("cards", "app.services.card_generator", "generate_cards"),
        ("compliance", "app.services.compliance_checker", "check_compliance"),
    ]:
        monkeypatch.setattr(importlib.import_module(module), name, AsyncMock(
            side_effect=error if stage == key else None, return_value=MagicMock(),
        ))
    if stage == "commit":
        db.commit.side_effect = error
    response = client.post("/api/topics/1/generate-draft", json={})
    assert response.status_code == 500
    assert "secret-original-document" not in response.text
    assert "sk-private-key" not in response.text


@pytest.mark.parametrize("entry,status,detail", [
    (ENDPOINTS[0], 422, "主题不能为空"),
    (ENDPOINTS[1], 404, "选题 #1 不存在"),
    (ENDPOINTS[2], 422, "素材标题不能为空"),
    (ENDPOINTS[3], 404, "草稿不存在"),
])
def test_existing_domain_errors_remain_actionable(api, monkeypatch, entry, status, detail):
    client, _ = api
    module, name, path, body = entry
    monkeypatch.setattr(importlib.import_module(module), name, AsyncMock(side_effect=ValueError(detail)))
    response = client.post(path, json=body)
    assert response.status_code == status
    assert response.json()["detail"] == detail

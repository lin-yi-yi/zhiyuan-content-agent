from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import learning
from app.services.learning_catalog import MODULES, SOURCES, get_catalog


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(learning.router, prefix="/api")
    return TestClient(app)


def test_catalog_has_complete_project_learning_route(client):
    result = client.get("/api/learning/catalog")
    assert result.status_code == 200
    data = result.json()
    assert {module["id"] for module in data["modules"]} == {
        "frontend", "backend", "rag", "agent", "saas", "evaluation", "connectors",
    }
    questions = [question for module in data["modules"] for question in module["questions"]]
    labs = [lab for module in data["modules"] for lab in module["labs"]]
    assert len(questions) >= 24 and len(labs) >= 8
    assert len({item["id"] for item in questions}) == len(questions)
    assert len({item["id"] for item in labs}) == len(labs)
    for question in questions:
        assert question["answer"] and question["followups"] and question["pitfall"]
        assert question["source_ids"] and set(question["source_ids"]).issubset(SOURCES)
    for lab in labs:
        assert [step["label"] for step in lab["steps"]] == ["解释", "复现", "故意改坏", "排错", "验收"]
        assert all(step["text"] for step in lab["steps"])
        assert set(lab["source_ids"]).issubset(SOURCES)
        assert lab["page"] in {"overview", "agent", "rag", "knowledge", "source-hub", "reports"}
    assert "不会上传学习答案" in data["progress_notice"]


@pytest.mark.parametrize("source_id", SOURCES)
def test_every_curated_source_exists_and_anchor_resolves(client, source_id):
    response = client.get(f"/api/learning/source/{source_id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["path"] == SOURCES[source_id][0]
    assert data["code"].strip()
    assert len(data["code"].splitlines()) <= 140
    assert data["end_line"] >= data["start_line"] >= 1
    assert not Path(data["path"]).is_absolute()
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("source_id", ["unknown", ".env", "passwd", "..%2F..%2F.env", "%2Fetc%2Fpasswd"])
def test_source_endpoint_does_not_accept_arbitrary_paths(client, source_id):
    response = client.get(f"/api/learning/source/{source_id}")
    assert response.status_code == 404
    assert "DATABASE_URL" not in response.text


def test_source_symlink_cannot_escape_repository(client, monkeypatch, tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    secret = tmp_path / "private.py"
    secret.write_text("private = 'not-for-this-endpoint'", encoding="utf-8")
    (repo / "public.py").symlink_to(secret)
    monkeypatch.setattr(learning, "ROOT", repo)
    monkeypatch.setitem(SOURCES, "test-source", ("public.py", "", 10))
    response = client.get("/api/learning/source/test-source")
    assert response.status_code == 404
    assert "not-for-this-endpoint" not in response.text


def test_stale_anchor_fails_without_returning_unrelated_code(client, monkeypatch, tmp_path):
    (tmp_path / "module.py").write_text("def replacement():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(learning, "ROOT", tmp_path)
    monkeypatch.setitem(SOURCES, "test-source", ("module.py", "def disappeared", 20))
    response = client.get("/api/learning/source/test-source")
    assert response.status_code == 409
    assert "replacement" not in response.text


def test_source_symlink_cannot_read_non_allowlisted_file_inside_repository(client, monkeypatch, tmp_path):
    (tmp_path / "user_notes.py").write_text("user_data = 'private-inside-repo'", encoding="utf-8")
    (tmp_path / "public.py").symlink_to(tmp_path / "user_notes.py")
    monkeypatch.setattr(learning, "ROOT", tmp_path)
    monkeypatch.setitem(SOURCES, "test-source", ("public.py", "", 20))
    response = client.get("/api/learning/source/test-source")
    assert response.status_code == 404
    assert "private-inside-repo" not in response.text


def test_catalog_mutations_do_not_modify_shared_curriculum():
    before = MODULES[0]["title"]
    data = get_catalog()
    data["modules"][0]["title"] = "changed"
    assert get_catalog()["modules"][0]["title"] == before


def test_catalog_is_honest_about_unimplemented_capabilities():
    modules = {module["id"]: module for module in get_catalog()["modules"]}
    assert "没有使用 LangGraph 原生 checkpointer" in modules["agent"]["limitations"]
    assert "未自主调用新闻 MCP" in modules["connectors"]["limitations"]
    assert "OCR" in modules["connectors"]["limitations"]
    assert "Reranker" in modules["rag"]["limitations"]


def test_external_readings_are_https_official_documentation():
    from urllib.parse import urlsplit
    allowed = {"react.dev", "fastapi.tiangolo.com", "docs.sqlalchemy.org", "docs.pydantic.dev",
               "qdrant.tech", "docs.langchain.com", "cheatsheetseries.owasp.org", "docs.pytest.org",
               "modelcontextprotocol.io"}
    for module in get_catalog()["modules"]:
        for doc in module["docs"]:
            parsed = urlsplit(doc["url"])
            assert parsed.scheme == "https" and parsed.hostname in allowed


def test_minimal_packaged_curriculum_sources_are_readable(client, monkeypatch, tmp_path):
    """Exercise lookup at a fresh runtime root without access to the full checkout."""
    import shutil
    repo = learning.ROOT
    for path, _, _ in SOURCES.values():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / path, target)
    monkeypatch.setattr(learning, "ROOT", tmp_path)
    for source_id in SOURCES:
        response = client.get(f"/api/learning/source/{source_id}")
        assert response.status_code == 200, response.text
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".data").exists()

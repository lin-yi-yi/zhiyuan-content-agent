"""Synthetic brand CRUD, immutable brief, concurrency and tenant boundaries."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
import sys

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.models  # noqa: F401
from app.agent_core.boundaries import get_knowledge_base_or_default, workspace_context
from app.api.routes import brands
from app.db.session import Base, get_db
from app.models.brand_profile import BrandProfile
from app.models.knowledge_base import KnowledgeBase
from app.models.workspace import Workspace
from app.services.business_brief import resolve_business_brief, workflow_templates
from test_saas_isolation import application, teams, invited_member, business_db


@pytest.fixture
def local_database(monkeypatch, tmp_path):
    monkeypatch.setenv("SAAS_MODE", "false")
    engine = create_engine(f"sqlite:///{tmp_path / 'brands.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        context = workspace_context(db)
        get_knowledge_base_or_default(db, context)
    yield factory
    engine.dispose()


@pytest.fixture
def local_client(local_database):
    app = FastAPI()
    app.include_router(brands.router, prefix="/api")
    def sessions():
        with local_database() as db:
            yield db
    app.dependency_overrides[get_db] = sessions
    with TestClient(app) as client:
        yield client


def create(client, **changes):
    result = client.post("/api/brands", json={"name": "合成工业产品", "audience": "采购与运维人员", **changes})
    assert result.status_code == 201, result.text
    return result.json()


def editable(profile, **changes):
    return {key: value for key, value in {**profile, **changes}.items()
            if key not in {"id", "created_at", "updated_at"}}


def business_request(**changes):
    values = dict(brand_profile_id=None, workflow_key=None, use_rag=True,
                  workspace_id=None, knowledge_base_id=None, provider="local")
    return SimpleNamespace(**{**values, **changes})


def test_profile_trims_strings_and_archives_without_deleting(local_client):
    item = create(local_client, name="  合成品牌  ", audience="  合成受众  ", tone="  清楚克制  ")
    assert item["name"] == "合成品牌" and item["audience"] == "合成受众" and item["tone"] == "清楚克制"
    assert item["version"] == 1 and item["knowledge_base_id"] == item["workspace_id"] == 1
    changed = local_client.put(f"/api/brands/{item['id']}", json=editable(item, is_active=False))
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == 2 and changed.json()["is_active"] is False
    assert len(local_client.get("/api/brands").json()) == 1
    assert local_client.get("/api/brands?include_archived=false").json() == []
    assert local_client.delete(f"/api/brands/{item['id']}").status_code == 405


@pytest.mark.parametrize("changes", [
    {"name": "  "}, {"name": "n" * 121}, {"audience": "a" * 1001}, {"tone": "t" * 1001},
    {"prohibited_claims": "p" * 2001}, {"call_to_action": "c" * 1001},
    {"data_policy": "secret"}, {"knowledge_base_id": 0}, {"workspace_id": -1}, {"role": "owner"},
])
def test_profile_rejects_invalid_or_unbounded_inputs(local_client, changes):
    response = local_client.post("/api/brands", json={"name": "Synthetic", **changes})
    assert response.status_code == 422
    assert local_client.get("/api/brands").json() == []


def test_profile_cannot_bind_a_knowledge_base_from_another_workspace(local_client, local_database):
    with local_database() as db:
        second = Workspace(name="Other", slug="other", is_default=False)
        db.add(second); db.flush()
        kb = KnowledgeBase(workspace_id=second.id, name="Other knowledge", status="active")
        db.add(kb); db.commit()
        foreign_kb, foreign_workspace = kb.id, second.id
    rejected = local_client.post("/api/brands", json={"name": "Synthetic", "knowledge_base_id": foreign_kb})
    assert rejected.status_code == 422
    other = create(local_client, workspace_id=foreign_workspace, knowledge_base_id=foreign_kb)
    assert local_client.get("/api/brands").json() == []
    assert local_client.get("/api/brands", params={"workspace_id": foreign_workspace}).json()[0]["id"] == other["id"]
    wrong_scope = editable(other); wrong_scope.pop("workspace_id"); wrong_scope["knowledge_base_id"] = 1
    assert local_client.put(f"/api/brands/{other['id']}", json=wrong_scope).status_code == 404


def test_stale_update_is_rejected_without_losing_newer_data(local_client):
    item = create(local_client)
    assert local_client.put(f"/api/brands/{item['id']}", json=editable(item, tone="New approved tone")).status_code == 200
    stale = local_client.put(f"/api/brands/{item['id']}", json=editable(item, tone="Stale overwrite"))
    assert stale.status_code == 409
    current = local_client.get("/api/brands").json()[0]
    assert current["tone"] == "New approved tone" and current["version"] == 2
    assert local_client.put(f"/api/brands/{item['id']}", json={"name": "Missing version"}).status_code == 422


def test_concurrent_writers_have_only_one_successful_compare_and_swap(local_client):
    item = create(local_client)
    barrier = Barrier(2)
    def write(tone):
        barrier.wait(timeout=5)
        return local_client.put(f"/api/brands/{item['id']}", json=editable(item, tone=tone)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["First writer", "Second writer"]))
    assert sorted(results) == [200, 409]
    assert local_client.get("/api/brands").json()[0]["version"] == 2


def test_brief_freezes_complete_profile_without_treating_it_as_fact(local_client, local_database):
    profile = create(local_client, prohibited_claims="不虚构销量", call_to_action="查阅规格")
    with local_database() as db:
        brief = resolve_business_brief(business_request(brand_profile_id=profile["id"]), db)
    assert brief["profile"] == profile
    assert brief["workflow"]["key"] == "knowledge_post"
    assert brief["knowledge_base_id"] == profile["knowledge_base_id"]
    assert brief["delivery"] == "图文交付包"
    assert brief["policy"] == {"facts_from_knowledge_only": True, "human_review_required": True, "automatic_publish": False}
    local_client.put(f"/api/brands/{profile['id']}", json=editable(profile, tone="New version"))
    assert brief["profile"]["version"] == 1 and brief["profile"]["tone"] == ""


@pytest.mark.parametrize("changes,code,message", [
    ({"use_rag": False}, 422, "资料依据"),
    ({"knowledge_base_id": 9001}, 422, "一致"),
    ({"workflow_key": "invented"}, 422, "流程不存在"),
    ({"brand_profile_id": 9001}, 404, "不存在"),
])
def test_invalid_business_brief_is_rejected_before_generation(local_client, local_database, changes, code, message):
    profile = create(local_client)
    values = {"brand_profile_id": profile["id"], **changes}
    with local_database() as db, pytest.raises(HTTPException) as caught:
        resolve_business_brief(business_request(**values), db)
    assert caught.value.status_code == code and message in caught.value.detail


def test_archived_or_inactive_knowledge_cannot_start_new_branded_tasks(local_client, local_database):
    profile = create(local_client, is_active=False)
    with local_database() as db:
        with pytest.raises(HTTPException, match="归档"):
            resolve_business_brief(business_request(brand_profile_id=profile["id"]), db)
        brand = db.get(BrandProfile, profile["id"]); brand.is_active = True
        kb = db.get(KnowledgeBase, profile["knowledge_base_id"]); kb.status = "archived"
        db.commit()
        with pytest.raises(HTTPException, match="可用的知识库"):
            resolve_business_brief(business_request(brand_profile_id=profile["id"]), db)


def test_local_only_brand_blocks_online_provider_and_allows_local(local_client, local_database):
    profile = create(local_client, data_policy="local_only")
    with local_database() as db:
        with pytest.raises(HTTPException, match="仅允许本地"):
            resolve_business_brief(business_request(brand_profile_id=profile["id"], provider="deepseek"), db)
        brief = resolve_business_brief(business_request(brand_profile_id=profile["id"], provider="local"), db)
        assert brief["profile"]["data_policy"] == "local_only"


def test_workflow_catalogue_and_unbranded_template_contract(local_client, local_database):
    templates = local_client.get("/api/brands/workflows").json()
    assert [item["key"] for item in templates] == ["knowledge_post", "product_faq", "case_story"]
    assert all(item["required_materials"] and item["instructions"] for item in templates)
    templates[0]["name"] = "Changed copy"
    assert workflow_templates()[0]["name"] != "Changed copy"
    with local_database() as db:
        assert resolve_business_brief(business_request(), db) is None
        brief = resolve_business_brief(business_request(workflow_key="product_faq"), db)
        assert brief["profile"] is None and brief["knowledge_base_id"] == 1
        assert brief["workflow"]["key"] == "product_faq"
        with pytest.raises(HTTPException, match="资料依据"):
            resolve_business_brief(business_request(workflow_key="case_story", use_rag=False), db)


def test_saas_brand_reads_require_identity(application):
    assert application.anonymous.get("/api/brands").status_code == 401
    assert application.anonymous.get("/api/brands/workflows").status_code == 401


def test_saas_organizations_keep_brand_profiles_separate_and_reject_forged_scope(application, teams):
    a, b = teams
    first, second = create(a.client, name="Organization A profile"), create(b.client, name="Organization B profile")
    assert first["id"] == second["id"]
    assert [x["name"] for x in a.client.get("/api/brands").json()] == ["Organization A profile"]
    assert [x["name"] for x in b.client.get("/api/brands").json()] == ["Organization B profile"]
    assert a.client.get("/api/brands", headers={"X-Organization-ID": b.org}).status_code == 403
    assert a.client.put(f"/api/brands/{second['id']}", json=editable(second), headers={"X-Organization-ID": b.org}).status_code == 403


@pytest.mark.parametrize("role", ["owner", "editor", "reviewer", "viewer"])
def test_saas_brand_writes_are_editor_or_owner_only_and_membership_is_current(application, teams, role):
    owner, _ = teams
    profile = create(owner.client)
    member = owner if role == "owner" else invited_member(application, owner, role)
    assert member.client.get("/api/brands").status_code == 200
    response = member.client.put(f"/api/brands/{profile['id']}", json=editable(profile, tone=f"Synthetic {role}"))
    assert response.status_code == (200 if role in {"owner", "editor"} else 403), response.text
    created = member.client.post("/api/brands", json={"name": "Second brand"})
    assert created.status_code == (201 if role in {"owner", "editor"} else 403), created.text
    if role != "owner":
        assert owner.client.patch(f"/api/saas/members/{member.membership}", json={"is_active": False}).status_code == 200
        assert member.client.get("/api/brands").status_code == 403


def test_saas_brand_write_requires_csrf_and_does_not_call_a_model(teams):
    from app.models.model_run import ModelRun
    owner, _ = teams
    assert owner.client.post("/api/brands", json={"name": "No CSRF"}, headers={"X-CSRF-Token": ""}).status_code == 403
    create(owner.client)
    with business_db(owner) as db:
        assert db.query(ModelRun).count() == 0

"""Full-app SaaS boundaries with real temporary control/tenant SQLite stores.

Semantic isolation uses synthetic vectors with real Qdrant, not a semantic-quality
claim. The background test runs the real local-rule workflow and model logging.
No dependency override, global module reload, external service or real account.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

ORIGIN = "http://testserver"
PASSWORD = "Synthetic-Integration-Only-0920"
CONTENT = "这是一份合成的组织资料，仅用于验证信息隔离与权限。内容不是新闻或客户信息。所有结论都需要可靠引用和人工核验。"


@pytest.fixture
def application(monkeypatch, tmp_path):
    monkeypatch.setenv("SAAS_MODE", "true")
    monkeypatch.setenv("SAAS_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("SAAS_SECURE_COOKIE", "false")
    monkeypatch.setenv("SAAS_ALLOW_REGISTRATION", "true")
    monkeypatch.setenv("SAAS_DATA_DIR", str(tmp_path / "saas"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "lexical")
    monkeypatch.setenv("RAG_VECTOR_PATH", str(tmp_path / "must-not-be-shared"))
    from app.saas import auth, store
    from app.saas.context import current_tenant
    from app.db.session import close_tenant_stores
    from app.agent_core.vector_store import close_vector_stores
    from app.llm.router import router as model_router
    from app.main import app

    monkeypatch.setattr(auth, "SCRYPT_N", 2 ** 12)  # Only temporary test hashes.
    monkeypatch.setattr(model_router, "_clients", {})
    auth.reset_rate_limits()
    close_vector_stores()
    close_tenant_stores()
    store.close_control_db()
    context_token = current_tenant.set(None)
    clients = []
    try:
        with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as anonymous:
            def new_client():
                client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
                clients.append(client)
                return client
            yield SimpleNamespace(app=app, anonymous=anonymous, new_client=new_client, root=tmp_path / "saas")
    finally:
        for client in clients:
            client.close()
        close_vector_stores()
        close_tenant_stores()
        store.close_control_db()
        auth.reset_rate_limits()
        current_tenant.reset(context_token)


def account(application, label):
    client = application.new_client()
    response = client.post("/api/saas/auth/register", json={"name": f"Synthetic {label}",
        "email": f"{label.lower()}@example.invalid", "password": PASSWORD, "organization_name": f"Organization {label}"})
    assert response.status_code == 201, response.text
    state = response.json()
    identity = SimpleNamespace(client=client, state=state, org=state["active_organization_id"],
                               user=state["user"]["id"], email=state["user"]["email"])
    client.headers.update({"X-CSRF-Token": state["csrf_token"], "X-Organization-ID": identity.org})
    return identity


@pytest.fixture
def teams(application):
    return account(application, "A"), account(application, "B")


@contextmanager
def business_db(identity):
    from app.db.session import SessionLocal
    from app.saas.context import TenantContext, current_tenant
    token = current_tenant.set(TenantContext(identity.org, identity.user, "owner"))
    try:
        with SessionLocal() as db:
            yield db
    finally:
        current_tenant.reset(token)


def seed_legacy(identity, marker):
    from app.models.source import Source
    from app.models.topic import Topic
    from app.models.draft import Draft
    from app.models.agent_run import AgentRun
    with business_db(identity) as db:
        source = Source(title=f"{marker}-source", raw_content=CONTENT, source_type="manual")
        db.add(source)
        db.flush()
        topic = Topic(title=f"{marker}-topic", source_id=source.id)
        db.add(topic)
        db.flush()
        draft = Draft(topic_id=topic.id, body_text=f"{marker}-draft")
        run = AgentRun(goal=f"{marker}-run", status="cancelled", current_step="cancelled")
        db.add_all([draft, run])
        db.commit()
        return {"source": source.id, "draft": draft.id, "run": run.id}


def upload(identity, marker="A", **extra):
    response = identity.client.post("/api/knowledge/documents", json={
        "title": f"{marker}-document", "content": marker + CONTENT, "format": "text", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def chat(identity, prompt="Synthetic request"):
    return identity.client.post("/api/models/chat", json={"provider": "local", "system_prompt": "Synthetic system",
                                                          "user_prompt": prompt})


@pytest.mark.parametrize("path", ["/api/sources", "/api/drafts", "/api/models/runs", "/api/agent-runs", "/api/knowledge/documents"])
def test_legacy_and_new_business_reads_require_login(application, path):
    assert application.anonymous.get(path).status_code == 401


def test_explicit_other_org_is_rejected_before_business_store_access(application, teams):
    a, b = teams
    response = a.client.get("/api/sources", headers={"X-Organization-ID": b.org})
    assert response.status_code == 403, response.text
    assert not (application.root / "tenants" / b.org).exists()
    response = a.client.get("/api/knowledge/documents", params={"workspace_id": 1, "knowledge_base_id": 1},
                            headers={"X-Organization-ID": b.org})
    assert response.status_code == 403


def test_legacy_sources_drafts_runs_and_model_logs_are_physically_isolated(teams):
    a, b = teams
    ids_a, ids_b = seed_legacy(a, "ALPHA"), seed_legacy(b, "BETA")
    assert ids_a == ids_b  # Equal numeric IDs are not a tenant boundary.
    assert chat(a, "alpha-model-marker").status_code == 200
    assert chat(b, "beta-model-marker").status_code == 200
    for identity, marker, other in [(a, "ALPHA", "BETA"), (b, "BETA", "ALPHA")]:
        for path in ["/api/sources", "/api/drafts", "/api/agent-runs"]:
            result = identity.client.get(path)
            assert result.status_code == 200, result.text
            assert marker in result.text and other not in result.text
        detail = identity.client.get(f"/api/drafts/{ids_a['draft']}")
        assert detail.json()["body_text"] == f"{marker}-draft"
        model_runs = identity.client.get("/api/models/runs").json()["runs"]
        assert len(model_runs) == 1
        expected = hashlib.sha256(("Synthetic system" + marker.lower() + "-model-marker").encode()).hexdigest()
        assert model_runs[0]["prompt_hash"] == expected
    deleted = a.client.delete(f"/api/drafts/{ids_a['draft']}")
    assert deleted.status_code == 204
    assert b.client.get(f"/api/drafts/{ids_b['draft']}").status_code == 200


@pytest.mark.parametrize("retrieval_mode", ["semantic", "hybrid"])
def test_same_document_ids_use_separate_real_qdrant_stores(application, teams, monkeypatch, retrieval_mode):
    from app.agent_core import rag_service, embeddings
    a, b = teams
    monkeypatch.setenv("RAG_RETRIEVAL_MODE", "semantic")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    monkeypatch.setattr(rag_service, "embed_texts", lambda texts, config, **kwargs: [[1.0, 0.0, 0.0] for _ in texts])
    first, second = upload(a, "ALPHA-ONLY"), upload(b, "BETA-ONLY")
    assert first["document"]["id"] == second["document"]["id"]
    paths = []
    for identity, marker, other in [(a, "ALPHA-ONLY", "BETA-ONLY"), (b, "BETA-ONLY", "ALPHA-ONLY")]:
        response = identity.client.post("/api/v04/rag/search", json={"query": marker, "top_k": 3, "retrieval_mode": retrieval_mode})
        assert response.status_code == 200, response.text
        assert response.json()["total"] > 0
        assert marker in response.text and other not in response.text
        if retrieval_mode == "hybrid":
            scores = response.json()["items"][0]["metadata"]["scores"]
            assert scores["semantic_rank"] == scores["bm25_rank"] == 1
        with business_db(identity):
            paths.append(embeddings.retrieval_config().vector_path)
    assert paths[0] != paths[1]
    assert all(str(application.root / "tenants") in path and Path(path).exists() for path in paths)


def invited_member(application, owner, role):
    member = account(application, role)
    created = owner.client.post("/api/saas/invites", json={"email": member.email, "role": role})
    assert created.status_code == 201, created.text
    accepted = member.client.post("/api/saas/invites/accept", json={"token": created.json()["token"]})
    assert accepted.status_code == 200, accepted.text
    member.org = owner.org
    member.client.headers["X-Organization-ID"] = owner.org
    rows = owner.client.get("/api/saas/members").json()["items"]
    member.membership = next(row["id"] for row in rows if row["user_id"] == member.user)
    return member


@pytest.mark.parametrize("role", ["viewer", "editor", "reviewer"])
def test_roles_and_revocation_apply_to_legacy_and_evidence_routes(application, teams, role):
    a, _ = teams
    ids = seed_legacy(a, "ROLE")
    member = invited_member(application, a, role)
    assert member.client.get("/api/sources").status_code == 200
    assert member.client.post("/api/v04/rag/search", json={"query": "合成问题"}).status_code == 200
    edit = member.client.put(f"/api/drafts/{ids['draft']}", json={"body_text": "合成修改"})
    assert edit.status_code == (200 if role == "editor" else 403), edit.text
    created = a.client.post("/api/evidence/notes", json={"title": "人工核验合成笔记", "content": CONTENT,
        "source_url": "https://example.com/synthetic", "rights_basis": "own", "citations": [
            {"claim": "合成声明", "excerpt": "合成引文", "source_url": "https://example.com/synthetic"}]})
    assert created.status_code == 201, created.text
    review = member.client.post(f"/api/evidence/notes/{created.json()['id']}/review",
        json={"decision": "verify", "confirmed_sources": True})
    assert review.status_code == (200 if role == "reviewer" else 403), review.text
    escalation = member.client.patch(f"/api/saas/members/{member.membership}", json={"role": "owner"})
    assert escalation.status_code == 403
    revoke = a.client.patch(f"/api/saas/members/{member.membership}", json={"is_active": False})
    assert revoke.status_code == 200, revoke.text
    assert member.client.get("/api/sources").status_code == 403
    assert member.client.post("/api/v04/rag/search", json={"query": "合成问题"}).status_code == 403


def test_cross_origin_and_missing_csrf_fail_before_mutation(teams):
    a, b = teams
    payload = {"title": "must-not-exist", "content": CONTENT}
    for headers in [{"Origin": "https://other.invalid"}, {"Origin": "null"},
                    {"X-CSRF-Token": ""}, {"X-CSRF-Token": b.state["csrf_token"]}]:
        response = a.client.post("/api/knowledge/documents", json=payload, headers=headers)
        assert response.status_code == 403, response.text
    assert a.client.get("/api/knowledge/documents").json()["total"] == 0
    assert a.client.get("/api/sources", headers={"Host": "other.invalid"}).status_code == 400


def test_disabled_model_cannot_use_previously_cached_client(teams, monkeypatch):
    from app.llm.router import ModelRouter, router
    a, b = teams
    config = {**ModelRouter.PROVIDERS, "deepseek": {"api_key": "synthetic-not-a-real-key",
        "model": "synthetic-model", "base_url": "https://provider.invalid"}}
    monkeypatch.setattr(ModelRouter, "PROVIDERS", config)
    calls = []
    class CachedClient:
        model = "synthetic-model"
        def chat(self, *args):
            calls.append(args)
            return "Synthetic response"
    router._clients["deepseek:synthetic-model"] = CachedClient()
    enabled = a.client.patch("/api/saas/connections/deepseek", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    payload = {"provider": "deepseek", "user_prompt": "Synthetic question"}
    assert a.client.post("/api/models/chat", json=payload).status_code == 200
    assert len(calls) == 1
    assert b.client.post("/api/models/chat", json=payload).status_code == 409
    assert a.client.patch("/api/saas/connections/deepseek", json={"enabled": False}).status_code == 200
    denied = a.client.post("/api/models/chat", json=payload)
    assert denied.status_code == 409, denied.text
    assert len(calls) == 1


def test_disabled_source_never_exposes_cached_results_or_fetches(teams, monkeypatch):
    import httpx
    from app.api.routes import github_sources
    from app.services import github_source
    from app.core.config import settings
    a, b = teams
    monkeypatch.setattr(settings, "GITHUB_ENABLED", True)
    monkeypatch.setattr(github_sources, "github_source", github_source.GithubSource())
    calls = []
    async def fetch(project, etag):
        calls.append(project)
        return httpx.Response(200, headers={"content-type": "application/json"}, json=[{
            "id": 71, "name": "Synthetic release", "tag_name": "v-test", "draft": False,
            "prerelease": False, "html_url": "https://github.com/langchain-ai/langchain/releases/tag/v-test",
            "published_at": "2026-09-19T00:00:00Z"}])
    monkeypatch.setattr(github_source, "_fetch", fetch)
    assert a.client.patch("/api/saas/connections/github", json={"enabled": True}).status_code == 200
    ready = a.client.get("/api/source-hub/github")
    assert ready.status_code == 200 and ready.json()["items"], ready.text
    assert len(calls) == 1
    assert a.client.patch("/api/saas/connections/github", json={"enabled": False}).status_code == 200
    for identity in (a, b):
        result = identity.client.get("/api/source-hub/github")
        assert result.status_code == 200, result.text
        assert result.json()["refresh"]["status"] == "disabled"
        assert result.json()["items"] == []
    assert len(calls) == 1


def test_ai_quota_is_per_org_and_failed_request_is_refunded(teams):
    from app.saas.commerce import provision_plan
    a, b = teams
    provision_plan(a.org, code="team", limits={"ai_requests": 1, "documents": 10, "members": 3})
    failed = chat(a, "")
    assert failed.status_code == 400, failed.text
    usage = a.client.get("/api/saas/billing").json()["usage"]["ai_requests"]
    assert usage["used"] == 0 and usage["refunded"] == 1 and usage["reserved"] == 0
    assert chat(a).status_code == 200
    usage = a.client.get("/api/saas/billing").json()["usage"]["ai_requests"]
    assert usage["used"] == 1 and usage["remaining"] == 0 and usage["reserved"] == 0
    assert chat(a).status_code == 429
    assert b.client.get("/api/saas/billing").json()["usage"]["ai_requests"]["used"] == 0
    assert chat(b).status_code == 200


def test_model_exception_keeps_uncertain_reservation_and_redacts_error(teams, monkeypatch):
    from app.llm.local import LocalRuleBasedClient
    from app.saas.context import current_tenant
    a, b = teams
    called = []
    def fail(self, *args, **kwargs):
        called.append(current_tenant.get().organization_id)
        raise RuntimeError("synthetic-secret-that-must-not-escape")
    monkeypatch.setattr(LocalRuleBasedClient, "chat", fail)
    failed = chat(a)
    assert failed.status_code in {500, 503}, failed.text
    assert "synthetic-secret" not in failed.text
    assert called == [a.org]
    usage = a.client.get("/api/saas/billing").json()["usage"]["ai_requests"]
    assert usage["used"] == 1 and usage["refunded"] == 0 and usage["reserved"] == 1
    assert b.client.get("/api/saas/billing").json()["usage"]["ai_requests"]["attempts"] == 0
    assert current_tenant.get() is None


def test_business_audit_uses_server_identity_and_keeps_tenants_separate(teams):
    a, b = teams
    upload(a, "ALPHA-AUDIT")
    upload(b, "BETA-AUDIT")
    for identity, other in [(a, b), (b, a)]:
        response = identity.client.get("/api/saas/audit")
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        mutations = [row for row in items if row["action"] == "api.mutation"
                     and row["resource"] == "/api/knowledge/documents"]
        assert len(mutations) == 1
        assert mutations[0]["actor_user_id"] == identity.user
        assert all(row["actor_user_id"] != other.user for row in items)
        assert "ALPHA-AUDIT" not in response.text and "BETA-AUDIT" not in response.text


def test_document_quota_allows_dedup_and_reindex_but_not_new_document(teams):
    from app.saas.commerce import provision_plan
    a, b = teams
    provision_plan(a.org, code="team", limits={"ai_requests": 10, "documents": 1, "members": 3})
    document = upload(a)["document"]
    assert upload(a)["deduplicated"] is True
    rebuilt = a.client.post(f"/api/knowledge/documents/{document['id']}/reindex")
    assert rebuilt.status_code == 200, rebuilt.text
    assert a.client.get("/api/saas/billing").json()["usage"]["documents"]["used"] == 1
    denied = a.client.post("/api/knowledge/documents", json={"title": "second", "content": "NEW" + CONTENT})
    assert denied.status_code == 429, denied.text
    assert a.client.get("/api/knowledge/documents").json()["total"] == 1
    assert upload(b, "independent")["document"]


def test_background_agent_keeps_original_org_during_other_org_request(teams, monkeypatch):
    from app.llm.local import LocalRuleBasedClient
    from app.saas.context import current_tenant
    a, b = teams
    entered, resume = Event(), Event()
    observed = []
    original = LocalRuleBasedClient._generate_draft
    def marked_draft(self, text):
        context = current_tenant.get()
        observed.append(context.organization_id if context else None)
        if context and context.organization_id == a.org:
            entered.set()
            assert resume.wait(10), "test did not release the background worker"
        result = original(self, text)
        result["body_text"] = "ALPHA-BACKGROUND-MARKER\n" + result["body_text"]
        return result
    monkeypatch.setattr(LocalRuleBasedClient, "_generate_draft", marked_draft)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(a.client.post, "/api/agent-runs", json={"goal": "合成后台任务",
            "provider": "local", "mode": "inspiration", "source_urls": [], "use_rag": False, "auto_score": False})
        try:
            assert entered.wait(10), "real background workflow did not reach generation"
            assert chat(b, "B-concurrent-request").status_code == 200
        finally:
            resume.set()
        response = future.result(timeout=20)
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    result = a.client.get(f"/api/agent-runs/{run_id}")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "awaiting_review", result.text
    assert "ALPHA-BACKGROUND-MARKER" in result.json()["draft"]["body_text"]
    assert observed and set(observed) == {a.org}
    assert b.client.get("/api/agent-runs").json() == []
    assert b.client.get("/api/drafts").json() == []
    assert b.client.get(f"/api/agent-runs/{run_id}").status_code == 404
    assert len(b.client.get("/api/models/runs").json()["runs"]) == 1
    assert len(a.client.get("/api/models/runs").json()["runs"]) >= 1
    assert current_tenant.get() is None

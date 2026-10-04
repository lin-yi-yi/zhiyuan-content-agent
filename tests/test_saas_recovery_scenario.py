"""HTTP scenario against temporary real SaaS SQLite/Qdrant stores.

Only embeddings and password hash work factors are synthetic. No model download,
external HTTP, production store, or restored client session is used.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
import saas_recovery_scenario as scenario
import saas_backup as backup

ORIGIN = "http://testserver"
SECRET = "SENSITIVE-response-cookie-password-do-not-report"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    values = {"PYTHON_DOTENV_DISABLED": "1", "SAAS_MODE": "true", "SAAS_PUBLIC_ORIGIN": ORIGIN,
        "SAAS_SECURE_COOKIE": "false", "SAAS_ALLOW_REGISTRATION": "true", "DATABASE_URL": "sqlite:///:memory:",
        "RAG_RETRIEVAL_MODE": "semantic", "RAG_EMBEDDING_PROVIDER": "fastembed",
        "RAG_EMBEDDING_MODEL": scenario.EMBEDDING_MODEL, "RAG_EMBEDDING_API_KEY": "",
        "RAG_EMBEDDING_CACHE_DIR": str(tmp_path / "unused-model-cache"), "AIHOT_ENABLED": "false", "GITHUB_ENABLED": "false",
        "MODEL_PRICING_JSON": "[]", "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}
    for provider in ("DEEPSEEK", "QWEN", "DOUBAO", "KIMI", "OPENAI", "LANGSMITH", "LANGCHAIN"):
        values[f"{provider}_API_KEY"] = ""
    for task in ("DEFAULT_LLM", "TOPIC_SCORE", "DRAFT_GENERATION", "CARD_GENERATION", "COMPLIANCE_CHECK"):
        values[f"{task}_PROVIDER"] = "local"
        values[f"{task}_MODEL"] = "local-rule-based-v0"
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    from app.saas import auth, store
    from app.saas.context import current_tenant
    from app.db.session import close_tenant_stores
    from app.agent_core import embeddings, rag_service, vector_store
    from app.llm.router import router as model_router
    from app.main import app
    import httpx

    monkeypatch.setattr(auth, "SCRYPT_N", 2 ** 12)
    monkeypatch.setattr(model_router, "_clients", {})
    real_config = embeddings.retrieval_config

    def synthetic_config(mode_override=None):
        return replace(real_config(mode_override), provider="synthetic", model=scenario.EMBEDDING_MODEL,
                       base_url="", api_key="")

    def forbidden(*args, **kwargs):
        raise AssertionError("External network/model loading is forbidden")

    monkeypatch.setattr(embeddings, "retrieval_config", synthetic_config)
    monkeypatch.setattr(rag_service, "retrieval_config", synthetic_config)
    monkeypatch.setattr(rag_service, "embed_texts", lambda texts, config, **kwargs: [[1.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(embeddings, "_fastembed_model", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    auth.reset_rate_limits()
    context_token = current_tenant.set(None)

    def close():
        vector_store.close_vector_stores()
        close_tenant_stores()
        store.close_control_db()

    @contextmanager
    def start(directory):
        close()
        monkeypatch.setenv("SAAS_DATA_DIR", str(directory))
        with TestClient(app, base_url=ORIGIN) as client:
            def transport(method, path, body=None, headers=None):
                # The scenario owns cookies. Do not reuse TestClient's jar across users.
                client.cookies.clear()
                response = client.request(method, path, json=body, headers=headers)
                return {"status": response.status_code, "body": response.text,
                        "headers": {key.title(): value for key, value in response.headers.items()}}
            try:
                yield transport
            finally:
                close()

    try:
        yield start
    finally:
        close()
        auth.reset_rate_limits()
        current_tenant.reset(context_token)


def test_real_backup_restore_and_original_volume_diverge(runtime, tmp_path):
    source, restored, archive = tmp_path / "source", tmp_path / "restored", tmp_path / "backup.zip"
    with runtime(source) as transport:
        state, initial = scenario.seed(transport, origin=ORIGIN)
        assert all(initial["checks"].values())
        assert initial["organizations"]["owner_a"]["roles"] == ["owner", "viewer"]
        assert initial["organizations"]["owner_b"]["roles"] == ["owner"]
        with pytest.raises(backup.BackupError, match="活跃"):
            backup.create_backup(source, archive, offline_confirm=True)
    backup.create_backup(source, archive, offline_confirm=True)
    with zipfile.ZipFile(archive) as bundle:
        assert sum(name.endswith("storage.sqlite") for name in bundle.namelist()) == 2
        assert sum(name.endswith("vectors/meta.json") for name in bundle.namelist()) == 2
        assert not any("model-cache" in name for name in bundle.namelist())
    with runtime(source) as transport:
        updated, changed = scenario.mutate(transport, state)
        assert len(state.documents["owner_a"]) == 1
        assert len(updated.documents["owner_a"]) == 2
        assert changed != initial
    backup.restore_backup(archive, restored, offline_confirm=True)
    with runtime(restored) as transport:
        assert scenario.verify(transport, state) == initial
    with runtime(source) as transport:
        assert scenario.verify(transport, updated) == changed
    assert not (tmp_path / "unused-model-cache").exists()


def test_public_signature_excludes_credentials_arbitrary_response_fields_and_restored_cookie(runtime, tmp_path):
    cookies, logins = [], []
    with runtime(tmp_path / "source") as raw:
        def transport(method, path, body=None, headers=None):
            if path == "/api/saas/auth/login":
                logins.append(headers["cookie"])
            response = raw(method, path, body, headers)
            value = json.loads(response["body"])
            value["internal_debug"] = SECRET
            response["body"] = json.dumps(value)
            for key, value in response["headers"].items():
                if key.lower() == "set-cookie":
                    cookies.append(value.split(";", 1)[0])
            return response
        state, first = scenario.seed(transport, origin=ORIGIN)
        assert scenario.verify(transport, state) == first
        rendered = json.dumps(first) + repr(state) + repr(state.accounts)
        assert SECRET not in rendered
        assert not any(account.password in rendered or account.email in rendered for account in state.accounts.values())
        assert not any(cookie in rendered for cookie in cookies)
        assert logins == [""] * 6
        assert len(cookies) == len(set(cookies)) == 9
        assert set(vars(state)) == {"origin", "accounts", "documents", "modified"}


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_cross_scope_hit_with_colliding_ids_is_not_a_success(runtime, tmp_path, mode):
    with runtime(tmp_path / "source") as raw:
        state, _ = scenario.seed(raw, origin=ORIGIN)
        def transport(method, path, body=None, headers=None):
            response = raw(method, path, body, headers)
            if path == "/api/v04/rag/search" and body["retrieval_mode"] == mode:
                value = json.loads(response["body"])
                value["items"][0]["content"] = scenario.document_body("owner_b")["content"]
                response["body"] = json.dumps(value)
            return response
        with pytest.raises(scenario.ScenarioError, match="^SEARCH_CROSSED_SCOPE$"):
            scenario.verify(transport, state)


def test_viewer_wrong_tenant_content_with_same_ids_is_rejected(runtime, tmp_path):
    with runtime(tmp_path / "source") as raw:
        state, _ = scenario.seed(raw, origin=ORIGIN)
        viewer_cookie = ""
        def transport(method, path, body=None, headers=None):
            nonlocal viewer_cookie
            response = raw(method, path, body, headers)
            if path == "/api/saas/auth/login" and body["email"] == state.accounts["viewer"].email:
                viewer_cookie = response["headers"]["Set-Cookie"].split(";", 1)[0]
            if path.startswith("/api/knowledge/documents/") and headers["cookie"] == viewer_cookie:
                value = json.loads(response["body"])
                value["content"] = scenario.document_body("owner_b")["content"]
                response["body"] = json.dumps(value)
            return response
        with pytest.raises(scenario.ScenarioError, match="^DOCUMENT_CONTENT_CHANGED$"):
            scenario.verify(transport, state)


def test_new_server_origin_override_does_not_change_expected_snapshot(runtime, tmp_path):
    with runtime(tmp_path / "source") as raw:
        state, first = scenario.seed(raw, origin=ORIGIN)
        state.origin = "http://127.0.0.1:12345"  # Former runtime address is no longer valid.
        assert scenario.verify(raw, state, origin=ORIGIN) == first
        updated, changed = scenario.mutate(raw, state, origin=ORIGIN)
        assert updated.origin == ORIGIN and state.origin != ORIGIN
        assert changed != first and not state.modified
        with pytest.raises(scenario.ScenarioError, match="^SCENARIO_ALREADY_MUTATED$"):
            scenario.mutate(raw, updated)


@pytest.mark.parametrize("fault,code", [("cross_org", "UNEXPECTED_HTTP_STATUS"),
    ("csrf", "UNEXPECTED_HTTP_STATUS"), ("viewer", "UNEXPECTED_HTTP_STATUS"),
    ("missing_vectors", "SEARCH_COUNT_CHANGED"), ("changed_usage", "USAGE_CHANGED"),
    ("changed_hash", "DOCUMENT_CONTENT_CHANGED"), ("missing_index_metadata", "VECTOR_COLLECTION_MISSING"),
    ("changed_role", "MEMBERSHIP_CHANGED"), ("external_call", "EXTERNAL_OR_EXTRA_MODEL_RUN")])
def test_report_fails_closed_for_acceptance_regressions(runtime, tmp_path, fault, code):
    with runtime(tmp_path / "source") as raw:
        state, _ = scenario.seed(raw, origin=ORIGIN)
        def transport(method, path, body=None, headers=None):
            response = raw(method, path, body, headers)
            value = json.loads(response["body"])
            if response["status"] == 403 and ((fault == "cross_org" and method == "GET")
                    or (fault == "csrf" and method == "POST" and not headers.get("x-csrf-token"))
                    or (fault == "viewer" and method == "POST" and headers.get("x-csrf-token"))):
                response["status"] = 200
            if fault == "missing_vectors" and path == "/api/v04/rag/search":
                value["items"], value["total"] = [], 0
            if fault == "changed_usage" and path == "/api/saas/billing":
                value["usage"]["ai_requests"]["used"] += 1
            if path.startswith("/api/knowledge/documents/"):
                if fault == "changed_hash":
                    value["content_hash"] = "0" * 64
                if fault == "missing_index_metadata":
                    value["metadata"]["vector_collection"] = ""
            if fault == "changed_role" and path == "/api/saas/members":
                value["items"][0]["role"] = "editor"
            if fault == "external_call" and path == "/api/models/runs":
                value["runs"][0]["provider"] = "openai"
            response["body"] = json.dumps(value)
            return response
        with pytest.raises(scenario.ScenarioError, match=f"^{code}$"):
            scenario.verify(transport, state)


@pytest.mark.parametrize("fault,code", [("exception", "HTTP_TRANSPORT_FAILED"),
    ("status", "UNEXPECTED_HTTP_STATUS"), ("invalid_json", "INVALID_HTTP_RESPONSE"),
    ("invalid_headers", "INVALID_HTTP_RESPONSE"), ("missing_cookie", "MISSING_AUTH_COOKIE")])
def test_transport_failures_never_include_raw_sensitive_details(fault, code):
    def transport(*args, **kwargs):
        if fault == "exception":
            raise RuntimeError(SECRET)
        response = {"status": 200, "body": "{}", "headers": {}}
        if fault == "status":
            response.update(status=500, body=SECRET)
        if fault == "invalid_json":
            response["body"] = SECRET
        if fault == "invalid_headers":
            response["headers"] = SECRET
        return response
    with pytest.raises(scenario.ScenarioError) as error:
        scenario.Scenario(transport).call("POST", "/api/saas/auth/login")
    assert str(error.value) == code
    assert SECRET not in str(error.value)


def test_driver_import_has_no_application_dependency():
    # The host orchestrator can import the driver without importing app/config or .env.
    import ast
    tree = ast.parse((ROOT / "scripts/saas_recovery_scenario.py").read_text())
    imported = {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {item.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for item in node.names}
    assert imported <= sys.stdlib_module_names

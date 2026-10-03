"""Real temporary SQLite concurrency and tenant-policy tests, no external APIs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from threading import Barrier

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.saas import commerce, connections, store
from app.saas.commerce_models import OrganizationConnection, UsageCounter, UsageEvent
from app.saas.context import TenantContext, current_tenant
from app.saas.models import Membership, Organization, User


ORG_A, ORG_B, USER = "a" * 32, "b" * 32, "c" * 32


@pytest.fixture
def control(monkeypatch, tmp_path):
    monkeypatch.setenv("SAAS_DATA_DIR", str(tmp_path / "control"))
    monkeypatch.delenv("AIHOT_COMMERCIAL_AUTHORIZED", raising=False)
    monkeypatch.setattr(connections.settings, "AIHOT_ENABLED", True)
    monkeypatch.setattr(connections.settings, "GITHUB_ENABLED", True)
    from app.llm.router import ModelRouter
    config = {provider: {**value, "api_key": "" if provider != "local" else "local"}
              for provider, value in ModelRouter.PROVIDERS.items()}
    monkeypatch.setattr(ModelRouter, "PROVIDERS", config)
    store.init_control_db()
    now = datetime.now(timezone.utc)
    with store.session_factory() as db:
        db.add(User(id=USER, email="synthetic@example.invalid", name="Synthetic", password_hash="not-a-password", created_at=now))
        db.add_all([Organization(id=ORG_A, name="Synthetic A", created_at=now),
                    Organization(id=ORG_B, name="Synthetic B", created_at=now)])
        db.flush()
        db.add_all([Membership(organization_id=org, user_id=USER, role="owner", created_at=now)
                    for org in (ORG_A, ORG_B)])
        db.commit()
    yield
    store.close_control_db()


def small_plan(org=ORG_A, count=3):
    return commerce.provision_plan(org, code="team", limits={"ai_requests": count, "documents": 10, "members": 3})


def test_trial_billing_is_measured_without_claiming_money_or_token_cost(control):
    result = commerce.billing_snapshot(ORG_A, document_count=4)
    assert result["plan"] == {"code": "trial", "name": "试用套餐"}
    assert result["limits"] == {"ai_requests": 100, "documents": 200, "members": 3}
    assert result["usage"]["documents"] == {"used": 4, "limit": 200, "measured": True}
    assert result["usage"]["members"] == {"used": 1, "limit": 3}
    assert result["usage"]["ai_requests"]["remaining"] == 100
    assert "后台任务后来失败不自动退回" in result["notice"]
    assert commerce.billing_snapshot(ORG_A)["usage"]["documents"]["used"] is None


def test_reserve_settle_refund_and_duplicate_completion_are_exactly_once(control):
    small_plan(count=2)
    first = commerce.reserve_usage(ORG_A, "request-private-marker-a")
    second = commerce.reserve_usage(ORG_A, "request-b")
    assert commerce.billing_snapshot(ORG_A)["usage"]["ai_requests"]["used"] == 2
    with pytest.raises(commerce.QuotaExceeded):
        commerce.reserve_usage(ORG_A, "request-c")
    assert commerce.finalize_usage(first, True)["status"] == "settled"
    assert commerce.finalize_usage(first, False)["status"] == "settled"
    assert commerce.finalize_usage(second, False)["status"] == "refunded"
    assert commerce.finalize_usage(second, True)["status"] == "refunded"
    usage = commerce.billing_snapshot(ORG_A)["usage"]["ai_requests"]
    assert usage == {"used": 1, "reserved": 0, "settled": 1, "remaining": 1, "attempts": 2, "refunded": 1}
    with pytest.raises(commerce.UsageConflict):
        commerce.reserve_usage(ORG_A, "request-b")
    commerce.reserve_usage(ORG_A, "request-c")
    snapshot = commerce.billing_snapshot(ORG_A)
    assert "request-private-marker" not in json.dumps(snapshot)
    assert first not in json.dumps(snapshot)
    with store.session_factory() as db:
        assert db.get(UsageEvent, first).request_hash != "request-private-marker-a"


def test_real_sqlite_concurrent_reservations_never_exceed_quota(control):
    small_plan(count=7)
    barrier = Barrier(16)

    def reserve(index):
        barrier.wait()
        try:
            return commerce.reserve_usage(ORG_A, f"concurrent-{index}")
        except commerce.QuotaExceeded:
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(reserve, range(16)))
    assert sum(token is not None for token in results) == 7
    assert commerce.billing_snapshot(ORG_A)["usage"]["ai_requests"]["used"] == 7
    with store.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(UsageEvent)) == 7


def test_concurrent_same_request_id_is_rejected_without_reexecution_permission(control):
    barrier = Barrier(12)

    def reserve(_):
        barrier.wait()
        try:
            return commerce.reserve_usage(ORG_A, "same-request")
        except commerce.UsageConflict:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(reserve, range(12)))
    assert sum(token is not None for token in results) == 1
    assert commerce.billing_snapshot(ORG_A)["usage"]["ai_requests"]["used"] == 1


def test_concurrent_completion_only_changes_the_counter_once(control):
    token = commerce.reserve_usage(ORG_A, "request-complete")
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: commerce.finalize_usage(token, True), range(12)))
    assert all(item["status"] == "settled" for item in results)
    assert commerce.billing_snapshot(ORG_A)["usage"]["ai_requests"]["settled"] == 1
    assert commerce.billing_snapshot(ORG_A)["usage"]["ai_requests"]["reserved"] == 0


def test_tenant_limits_counters_and_identical_request_ids_are_isolated(control):
    small_plan(ORG_A, count=1)
    commerce.reserve_usage(ORG_A, "same-reference")
    with pytest.raises(commerce.QuotaExceeded):
        commerce.reserve_usage(ORG_A, "second")
    commerce.reserve_usage(ORG_B, "same-reference")
    assert commerce.billing_snapshot(ORG_B)["usage"]["ai_requests"]["remaining"] == 99
    assert commerce.billing_snapshot(ORG_B)["plan"]["code"] == "trial"


def test_month_rollover_finalizes_original_period_without_spending_new_month(control, monkeypatch):
    monkeypatch.setattr(commerce, "_now", lambda: datetime(2026, 12, 31, tzinfo=timezone.utc))
    token = commerce.reserve_usage(ORG_A, "old-period")
    monkeypatch.setattr(commerce, "_now", lambda: datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert commerce.finalize_usage(token, True)["period"] == "2026-12"
    result = commerce.billing_snapshot(ORG_A)
    assert result["period"]["label"] == "2027-01"
    assert result["usage"]["ai_requests"]["used"] == 0
    with store.session_factory() as db:
        assert db.get(UsageCounter, (ORG_A, "2026-12", "ai_requests")).settled == 1


@pytest.mark.parametrize("kwargs", [
    {"amount": 0}, {"amount": True}, {"amount": 1001}, {"metric": "tokens"},
])
def test_invalid_usage_request_does_not_write(control, kwargs):
    with pytest.raises(commerce.CommerceError):
        commerce.reserve_usage(ORG_A, "invalid-request", **kwargs)
    assert commerce.billing_snapshot(ORG_A)["usage"]["ai_requests"]["used"] == 0


def test_upgrade_requests_are_idempotent_and_cannot_grant_team_rights(control):
    first = commerce.request_upgrade(ORG_A, USER, note="Synthetic request")
    second = commerce.request_upgrade(ORG_A, USER, note="Another click")
    assert first == second
    assert first["status"] == "requested"
    snapshot = commerce.billing_snapshot(ORG_A)
    assert snapshot["plan"]["code"] == "trial"
    assert snapshot["limits"]["ai_requests"] == 100
    assert snapshot["upgrade_requests"] == [first]
    assert commerce.billing_snapshot(ORG_B)["upgrade_requests"] == []
    with pytest.raises(commerce.CommerceError):
        commerce.provision_plan(ORG_A, code="team", limits={"ai_requests": 1000})


def test_connection_cards_distinguish_real_file_import_and_planned_integrations(control):
    cards = {card["provider"]: card for card in connections.connection_snapshot(ORG_A, owner=True)["connections"]}
    for provider in ("markdown", "obsidian", "local"):
        assert cards[provider]["effective_enabled"]
    for provider in ("feishu", "wecom"):
        assert cards[provider]["status"] == "planned"
        assert not cards[provider]["supported"] and not cards[provider]["can_toggle"]
        with pytest.raises(connections.ConnectionUnavailable):
            connections.update_connection(ORG_A, provider, enabled=True)
    assert cards["deepseek"]["status"] == "not_configured"
    assert cards["deepseek"]["credential_source"] == "operator_environment"
    assert not cards["github"]["enabled"]


def test_aihot_organization_switch_cannot_grant_or_retain_commercial_authorization(control, monkeypatch):
    with pytest.raises(connections.ConnectionUnavailable, match="商业授权"):
        connections.update_connection(ORG_A, "aihot", enabled=True)
    monkeypatch.setenv("AIHOT_COMMERCIAL_AUTHORIZED", "true")
    connections.update_connection(ORG_A, "aihot", enabled=True)
    assert connections.require_connection("aihot", organization_id=ORG_A)["effective_enabled"]
    monkeypatch.delenv("AIHOT_COMMERCIAL_AUTHORIZED")
    state = connections.connection_state("aihot", organization_id=ORG_A)
    assert state["enabled"] and not state["effective_enabled"]
    assert state["status"] == "authorization_required"
    with pytest.raises(connections.ConnectionUnavailable):
        connections.require_connection("aihot", organization_id=ORG_A)
    connections.update_connection(ORG_A, "aihot", enabled=False)


def test_source_operator_flag_and_org_setting_both_enforced(control, monkeypatch):
    connections.update_connection(ORG_A, "github", enabled=True)
    assert connections.require_connection("github", organization_id=ORG_A)
    with pytest.raises(connections.ConnectionUnavailable):
        connections.require_connection("github", organization_id=ORG_B)
    monkeypatch.setattr(connections.settings, "GITHUB_ENABLED", False)
    with pytest.raises(connections.ConnectionUnavailable, match="运营方"):
        connections.require_connection("github", organization_id=ORG_A)


def test_model_configuration_is_masked_not_probed_and_default_selection_is_scoped(control, monkeypatch):
    from app.llm.router import ModelRouter
    monkeypatch.setitem(ModelRouter.PROVIDERS["deepseek"], "api_key", "sk-SYNTHETIC-DO-NOT-LEAK")
    monkeypatch.setitem(ModelRouter.PROVIDERS["qwen"], "api_key", "sk-SECOND-SYNTHETIC")
    connections.update_connection(ORG_A, "deepseek", enabled=True, is_default=True)
    snapshot = connections.connection_snapshot(ORG_A, owner=True)
    assert "sk-SYNTHETIC" not in json.dumps(snapshot)
    assert "api_key" not in json.dumps(snapshot)
    assert connections.enforce_provider(organization_id=ORG_A) == "deepseek"
    assert connections.enforce_provider(organization_id=ORG_B) == "local"
    with pytest.raises(connections.ConnectionUnavailable):
        connections.enforce_provider("deepseek", organization_id=ORG_B)
    connections.update_connection(ORG_A, "qwen", enabled=True, is_default=True)
    with store.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(OrganizationConnection).where(
            OrganizationConnection.organization_id == ORG_A, OrganizationConnection.is_default.is_(True))) == 1
    assert connections.get_default_model_provider(ORG_A) == "qwen"
    connections.update_connection(ORG_A, "qwen", enabled=False)
    assert connections.get_default_model_provider(ORG_A) == "local"
    monkeypatch.setitem(ModelRouter.PROVIDERS["deepseek"], "api_key", "")
    with pytest.raises(connections.ConnectionUnavailable, match="凭证"):
        connections.enforce_provider("deepseek", organization_id=ORG_A)


def test_runtime_helpers_use_authenticated_context_and_reject_unknown_provider(control):
    handle = current_tenant.set(TenantContext(ORG_A, USER, "owner"))
    try:
        assert connections.enforce_provider() == "local"
        assert connections.require_connection("markdown")["effective_enabled"]
        with pytest.raises(connections.ConnectionUnavailable):
            connections.enforce_provider("https://arbitrary.example.invalid")
    finally:
        current_tenant.reset(handle)


@pytest.fixture
def client(control, monkeypatch):
    from app.saas.commerce_routes import router
    from app.db import session as business
    monkeypatch.setattr(business, "count_tenant_documents", lambda org: 4 if org == ORG_A else 8)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        handle = current_tenant.set(TenantContext(request.headers.get("x-test-org", ORG_A), USER,
                                                  request.headers.get("x-test-role", "owner")))
        try:
            return await call_next(request)
        finally:
            current_tenant.reset(handle)

    app.include_router(router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("role", ["editor", "reviewer", "viewer"])
def test_only_owner_can_change_connections_or_request_upgrade(client, role):
    headers = {"x-test-role": role}
    snapshot = client.get("/api/saas/connections", headers=headers)
    assert snapshot.status_code == 200
    assert not any(card["can_toggle"] for card in snapshot.json()["connections"])
    assert client.patch("/api/saas/connections/github", json={"enabled": True}, headers=headers).status_code == 403
    assert client.post("/api/saas/billing/upgrade-request", json={}, headers=headers).status_code == 403


def test_owner_api_is_scoped_rejects_credential_writes_and_does_not_charge(client):
    assert client.patch("/api/saas/connections/github", json={"enabled": True}).status_code == 200
    other = client.get("/api/saas/connections", headers={"x-test-org": ORG_B}).json()
    assert not next(card for card in other["connections"] if card["provider"] == "github")["enabled"]
    assert client.patch("/api/saas/connections/deepseek", json={"api_key": "secret"}).status_code == 422
    assert client.patch("/api/saas/connections/github", json={"enabled": "true"}).status_code == 422
    assert client.patch("/api/saas/connections/github", json={"organization_id": ORG_B, "enabled": True}).status_code == 422
    assert client.patch("/api/saas/connections/feishu", json={"enabled": True}).status_code == 409
    assert client.post("/api/saas/billing/upgrade-request", json={"requested_plan": "team"}).status_code == 201
    billing = client.get("/api/saas/billing").json()
    assert billing["plan"]["code"] == "trial"
    assert billing["usage"]["documents"]["used"] == 4
    assert billing["usage"]["ai_requests"]["used"] == 0
    assert client.post("/api/saas/billing/upgrade-request", json={"requested_plan": "unlimited"}).status_code == 422

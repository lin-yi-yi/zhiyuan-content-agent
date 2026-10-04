"""Real temporary ledgers, trusted identity, and injected receipt/transport failures."""
import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core import diagnostics
from app.db.session import SessionLocal
from app.llm.local import LocalRuleBasedClient
from app.models.source import Source
from app.saas import auth, commerce, store
from app.saas.commerce_models import UsageCounter, UsageEvent
from app.saas.context import current_tenant
from app.saas.middleware import SaaSMiddleware

from test_diagnostics import events
from test_saas_isolation import application, teams, business_db


SECRET = "SYNTHETIC-FINALIZATION-PRIVATE-219af"


def ledger(team, request_id):
    with store.session_factory() as db:
        item = db.scalars(select(UsageEvent).where(UsageEvent.organization_id == team.org,
            UsageEvent.request_hash == hashlib.sha256(request_id.encode()).hexdigest())).one()
        counter = db.get(UsageCounter, (team.org, item.period, item.metric))
        return item.status, counter.reserved, counter.settled, item.token


async def respond(scope, receive, send, status=201):
    await send({"type": "http.response.start", "status": status, "headers": []})
    await send({"type": "http.response.body", "body": b"synthetic response"})


def drive(team, endpoint=respond, *, status=201, send_failure=False, full_app=None):
    """Use real auth and quota code in the caller task, so leaked ContextVars are observable."""
    async def scenario():
        parent = current_tenant.get()
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/models/chat",
            "query_string": f"private={SECRET}".encode(), "root_path": "", "state": {},
            "headers": [(b"host", b"testserver"), (b"origin", b"http://testserver"),
                (b"cookie", "; ".join(f"{key}={value}" for key, value in team.client.cookies.items()).encode()),
                (b"x-organization-id", team.org.encode()),
                (b"x-csrf-token", team.state["csrf_token"].encode()),
                (b"content-type", b"application/json")]}
        async def receive():
            return {"type": "http.request", "body": json.dumps({"private": SECRET,
                "provider": "local", "user_prompt": "Synthetic request"}).encode(), "more_body": False}
        sent = []
        async def send(message):
            if send_failure:
                raise RuntimeError(SECRET)
            sent.append(message)
        async def app(scope, receive, send):
            assert current_tenant.get().organization_id == team.org
            await endpoint(scope, receive, send, status)
        stack = full_app or diagnostics.RequestDiagnosticsMiddleware(SaaSMiddleware(app))
        error = None
        try:
            await stack(scope, receive, send)
        except BaseException as exc:
            error = exc
        assert current_tenant.get() == parent
        assert diagnostics.current_request_id.get() is None
        return SimpleNamespace(request_id=scope["state"]["request_id"], sent=sent, error=error)
    return asyncio.run(scenario())


def records_for(events, request_id):
    return [item for item in events() if item["request_id"] == request_id]


def assert_private(events, token):
    value = json.dumps(events(), ensure_ascii=False)
    assert SECRET not in value and token not in value and "Traceback" not in value


@pytest.mark.parametrize("status,outcome", [(201, "accepted"), (422, "rejected")])
@pytest.mark.parametrize("committed", [False, True])
def test_finalization_failure_preserves_business_response_and_never_reverses_it(application, teams, monkeypatch, events,
                                                                              status, outcome, committed):
    real = commerce.finalize_usage
    calls = []
    def fail(token, succeeded):
        calls.append(succeeded)
        if committed:
            real(token, succeeded)  # Simulate a lost receipt after a real commit.
        raise type(SECRET, (Exception,), {})(SECRET)
    monkeypatch.setattr(commerce, "finalize_usage", fail)
    result = drive(teams[0], status=status)
    assert result.error is None
    assert next(item["status"] for item in result.sent if item["type"] == "http.response.start") == status
    expected = ("settled", 0, 1) if outcome == "accepted" else ("refunded", 0, 0)
    actual = ledger(teams[0], result.request_id)
    assert actual[:3] == (expected if committed else ("reserved", 1, 0))
    assert calls == [outcome == "accepted"]
    failures = [item for item in records_for(events, result.request_id) if item["event"] == "usage_finalization_failed"]
    assert len(failures) == 1 and failures[0]["usage_outcome"] == outcome
    assert failures[0]["code"] == "USAGE_FINALIZATION_UNCONFIRMED"
    assert failures[0]["error_type"] == "UnexpectedError"
    assert "reservation_status" not in failures[0]  # No assertion about an unconfirmed commit.
    assert_private(events, actual[3])


@pytest.mark.parametrize("later_failure", ["audit", "send"])
@pytest.mark.parametrize("finalize_fails", [False, True])
@pytest.mark.parametrize("use_main", [False, True])
def test_audit_or_send_failure_cannot_refund_an_accepted_attempt(application, teams, monkeypatch, events,
                                                              later_failure, finalize_fails, use_main):
    real = commerce.finalize_usage
    calls = []
    def finalize(token, succeeded):
        calls.append(succeeded)
        if finalize_fails:
            raise OSError(SECRET)
        return real(token, succeeded)
    monkeypatch.setattr(commerce, "finalize_usage", finalize)
    if later_failure == "audit":
        def failed_audit(*args, **kwargs):
            raise RuntimeError(SECRET)
        monkeypatch.setattr(auth, "record_audit", failed_audit)
    result = drive(teams[0], send_failure=later_failure == "send", full_app=application.app if use_main else None)
    assert result.error is None and calls == [True]
    actual = ledger(teams[0], result.request_id)
    assert actual[:3] == (("reserved", 1, 0) if finalize_fails else ("settled", 0, 1))
    if later_failure == "audit":
        assert result.sent[0]["status"] == 503
        assert any(item["event"] == "http_response" and item["http_status"] == 503
                   and item["usage_outcome"] == "accepted" for item in records_for(events, result.request_id))
    if finalize_fails:
        failures = [item for item in records_for(events, result.request_id) if item["event"] == "usage_finalization_failed"]
        assert len(failures) == 1 and failures[0]["usage_outcome"] == "accepted"
    assert not any(item["event"] == "usage_outcome_unknown" for item in records_for(events, result.request_id))
    assert_private(events, actual[3])


@pytest.mark.parametrize("failure", ["exception", "disconnect", "cancel"])
def test_no_business_response_keeps_unknown_reservation_and_always_resets_context(application, teams, monkeypatch, events, failure):
    calls = []
    monkeypatch.setattr(commerce, "finalize_usage", lambda *args: calls.append(args))
    async def interrupted(scope, receive, send, status):
        if failure == "exception":
            raise RuntimeError(SECRET)
        if failure == "cancel":
            raise asyncio.CancelledError(SECRET)
        return  # A disconnected handler has produced no response decision.
    result = drive(teams[0], interrupted)
    assert isinstance(result.error, asyncio.CancelledError) if failure == "cancel" else result.error is None
    assert calls == []
    actual = ledger(teams[0], result.request_id)
    assert actual[:3] == ("reserved", 1, 0)
    unknown = [item for item in records_for(events, result.request_id) if item["event"] == "usage_outcome_unknown"]
    assert len(unknown) == 1 and unknown[0]["usage_outcome"] == "unknown"
    assert unknown[0]["organization_id"] == teams[0].org
    assert_private(events, actual[3])


def test_cancelled_finalization_does_not_retry_or_leak_context(application, teams, monkeypatch, events):
    calls = []
    def cancelled(token, succeeded):
        calls.append(succeeded)
        raise asyncio.CancelledError(SECRET)
    monkeypatch.setattr(commerce, "finalize_usage", cancelled)
    result = drive(teams[0])
    assert isinstance(result.error, asyncio.CancelledError) and calls == [True]
    actual = ledger(teams[0], result.request_id)
    assert actual[:3] == ("reserved", 1, 0)
    failure, = [item for item in records_for(events, result.request_id) if item["event"] == "usage_finalization_failed"]
    assert failure["usage_outcome"] == "accepted"
    assert_private(events, actual[3])


def test_cleanup_resets_tenant_even_if_unknown_outcome_diagnostic_itself_raises(application, teams, monkeypatch):
    real_emit = diagnostics.emit_diagnostic
    def emit(event, **kwargs):
        if event == "usage_outcome_unknown":
            raise asyncio.CancelledError(SECRET)
        real_emit(event, **kwargs)
    monkeypatch.setattr(diagnostics, "emit_diagnostic", emit)
    async def no_response(scope, receive, send, status):
        return
    result = drive(teams[0], no_response)
    assert isinstance(result.error, asyncio.CancelledError)
    assert ledger(teams[0], result.request_id)[:3] == ("reserved", 1, 0)


def test_real_main_fallback_500_after_committed_side_effect_is_unknown(application, teams, monkeypatch, events):
    def commit_then_fail(*args, **kwargs):
        with SessionLocal() as db:
            db.add(Source(title="synthetic committed side effect", raw_content=SECRET, source_type="manual"))
            db.commit()
        raise RuntimeError(SECRET)
    monkeypatch.setattr(LocalRuleBasedClient, "chat", commit_then_fail)
    team = teams[0]
    response = team.client.post("/api/models/chat", json={"provider": "local", "user_prompt": SECRET})
    assert response.status_code == 500
    correlation = response.headers["X-Request-ID"]
    with business_db(team) as db:
        assert db.query(Source).filter_by(title="synthetic committed side effect").count() == 1
    actual = ledger(team, correlation)
    assert actual[:3] == ("reserved", 1, 0)
    assert any(item["event"] == "usage_outcome_unknown" for item in records_for(events, correlation))
    assert SECRET not in response.text
    assert_private(events, actual[3])


@pytest.mark.parametrize("status", [201, 422])
def test_real_main_explicit_responses_finalize_and_client_cannot_inject_internal_flag(application, teams, events, status):
    team = teams[0]
    if status == 201:
        response = team.client.post("/api/agent-runs", headers={"X-Business-Outcome-Unknown": "true"},
            json={"provider": "local", "goal": "synthetic attempt", "auto_score": False,
                  "_business_outcome_unknown": True})
    else:
        response = team.client.post("/api/models/chat", headers={"X-Business-Outcome-Unknown": "true"},
                                   json={"user_prompt": [], "_business_outcome_unknown": True})
    assert response.status_code == status
    correlation = response.headers["X-Request-ID"]
    assert ledger(team, correlation)[:3] == (("settled", 0, 1) if status == 201 else ("refunded", 0, 0))
    assert not any(item["event"].startswith("usage_") for item in records_for(events, correlation))


def test_usage_diagnostic_rejects_arbitrary_outcome_text(events):
    diagnostics.emit_diagnostic("usage_finalization_failed", usage_outcome=SECRET, code=SECRET, error_type=SECRET)
    record, = events()
    assert record["usage_outcome"] is None and record["code"] is None and record["error_type"] is None
    assert SECRET not in json.dumps(record)


@pytest.mark.parametrize("finalize_fails", [False, True])
def test_failed_diagnostic_sink_preserves_business_acceptance(application, teams, monkeypatch, finalize_fails):
    calls = []
    real = commerce.finalize_usage
    def finalize(token, succeeded):
        calls.append(succeeded)
        if finalize_fails:
            raise OSError(SECRET)
        return real(token, succeeded)
    monkeypatch.setattr(commerce, "finalize_usage", finalize)
    def failed_log(*args, **kwargs):
        raise OSError(SECRET)
    monkeypatch.setattr(diagnostics.LOGGER, "log", failed_log)
    result = drive(teams[0], full_app=application.app)
    assert result.error is None and calls == [True]
    assert result.sent[0]["status"] == 200
    assert ledger(teams[0], result.request_id)[:3] == (("reserved", 1, 0) if finalize_fails else ("settled", 0, 1))


def test_concurrent_tenants_and_recovery_keep_uncertain_history(application, teams, monkeypatch, events):
    real = commerce.finalize_usage
    concurrent = Barrier(2)
    calls = []
    def finalize(token, succeeded):
        organization_id = current_tenant.get().organization_id
        calls.append((organization_id, succeeded))
        concurrent.wait(timeout=5)
        if organization_id == teams[0].org:
            raise OSError(SECRET)
        return real(token, succeeded)
    monkeypatch.setattr(commerce, "finalize_usage", finalize)
    def request(team):
        return team.client.post("/api/models/chat", json={"provider": "local", "user_prompt": "Synthetic request"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(request, teams))
    assert all(response.status_code == 200 for response in responses)
    assert sorted(calls) == sorted((team.org, True) for team in teams)
    ids = [response.headers["X-Request-ID"] for response in responses]
    assert ids[0] != ids[1]
    assert ledger(teams[0], ids[0])[:3] == ("reserved", 1, 0)
    assert ledger(teams[1], ids[1])[:3] == ("settled", 0, 1)
    for team, correlation in zip(teams, ids):
        records = records_for(events, correlation)
        assert records and all(item["organization_id"] == team.org for item in records)
    monkeypatch.setattr(commerce, "finalize_usage", real)
    recovered = request(teams[0])
    assert recovered.status_code == 200
    assert ledger(teams[0], recovered.headers["X-Request-ID"])[:3] == ("settled", 1, 1)
    assert ledger(teams[0], ids[0])[:3] == ("reserved", 1, 1)
    assert current_tenant.get() is None and diagnostics.current_request_id.get() is None
    assert_private(events, ledger(teams[0], ids[0])[3])

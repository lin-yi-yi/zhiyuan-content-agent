"""Synthetic metadata tracing, pricing, migration and tenant isolation tests.

All providers use stubs or httpx.MockTransport; no API key or paid call is used.
"""
import asyncio
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db import session
from app.db.init_db import _ensure_model_usage_columns
from app.llm.local import LocalRuleBasedClient
from app.llm.openai_compatible import ModelCallError, OpenAICompatibleClient
from app.llm.tracing import (
    current_model_trace, estimate_model_cost, model_trace_context, serialize_model_run,
)
from app.models.model_run import ModelRun
from app.saas.context import TenantContext, current_tenant

from test_model_client import client_with_results, completion, logs


def price_config(**changes):
    return json.dumps([{
        "provider": "test", "model": "test-model", "version": "synthetic-2026-10",
        "currency": "CNY", "input_per_million": "1.5", "output_per_million": "3",
        **changes,
    }])


@pytest.fixture(autouse=True)
def no_real_prices(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PRICING_JSON", "[]")


def rows(logs):
    return [db.rows[0] for db in logs]


def test_trace_context_restores_nested_and_failed_scopes_without_leaking_to_unrelated_calls(logs):
    client, _ = client_with_results([completion("ok")] * 4)
    assert current_model_trace.get() is None
    with model_trace_context(10, 101, "generate_draft", 2):
        client.chat("outer system", "first")
        with pytest.raises(RuntimeError):
            with model_trace_context(20, 201, "score_topic", 1):
                client.chat("inner system", "second")
                raise RuntimeError("synthetic scope failure")
        client.chat("outer system", "third")
    client.chat("standalone system", "fourth")
    result = rows(logs)
    assert [row.agent_run_id for row in result] == [10, 20, 10, None]
    assert [row.agent_step_id for row in result] == [101, 201, 101, None]
    assert [row.workflow_attempt for row in result] == [2, 1, 2, None]
    assert len({row.invocation_id for row in result}) == 4
    assert [row.request_index for row in result] == [1] * 4
    assert current_model_trace.get() is None


def test_async_tasks_keep_trace_context_separate_even_with_same_cached_client(logs):
    client, _ = client_with_results([completion("ok")] * 2)

    async def call(run_id):
        with model_trace_context(run_id, run_id * 10, "score_topic", run_id):
            await asyncio.sleep(0)
            client.chat("same system", "same user")

    async def concurrent():
        await asyncio.gather(call(1), call(2))
    asyncio.run(concurrent())
    assert {(row.agent_run_id, row.agent_step_id, row.workflow_attempt) for row in rows(logs)} == {(1, 10, 1), (2, 20, 2)}
    assert current_model_trace.get() is None


def test_json_format_fallback_shares_invocation_but_counts_separate_requests(logs):
    client, calls = client_with_results([
        RuntimeError("response_format is unsupported and private payload must stay hidden"),
        completion('{"ok": true}'),
    ])
    with model_trace_context(11, 12, "topic_ideas", 3):
        assert client.chat_json("template", "user") == {"ok": True}
    first, second = rows(logs)
    assert first.invocation_id == second.invocation_id
    assert [first.request_index, second.request_index] == [1, 2]
    assert [first.call_kind, second.call_kind] == ["initial", "format_fallback"]
    assert [first.success, second.success] == [False, True]
    assert first.workflow_attempt == second.workflow_attempt == 3
    assert len(calls) == 2


def test_json_repair_and_retry_versions_are_hashes_of_actual_system_prompts(logs):
    client, calls = client_with_results([completion("invalid synthetic output")] * 5)
    with model_trace_context(1, 2, "generate_draft", 4):
        with pytest.raises(ValueError):
            client.chat_json("original system template", "secret user input")
    result = rows(logs)
    assert len({row.invocation_id for row in result}) == 1
    assert [row.request_index for row in result] == [1, 2, 3, 4, 5]
    assert [row.call_kind for row in result] == ["initial", "json_repair", "json_retry", "json_repair", "json_retry"]
    assert all(row.workflow_attempt == 4 for row in result)
    for row, call in zip(result, calls):
        assert row.prompt_version == hashlib.sha256(call["messages"][0]["content"].encode()).hexdigest()
    assert result[0].prompt_version != result[1].prompt_version
    assert "secret" not in str([serialize_model_run(row) for row in result])


def test_sdk_constructor_disables_hidden_retries(monkeypatch):
    captured = {}
    monkeypatch.setattr("app.llm.openai_compatible.OpenAI", lambda **kwargs: captured.update(kwargs))
    OpenAICompatibleClient("synthetic-test-key", "https://provider.example/v1", "test-model", "test")
    assert captured["max_retries"] == 0 and captured["timeout"] == 120.0


def test_installed_sdk_sends_no_hidden_retry_on_server_failure(logs, monkeypatch):
    import httpx
    from openai import OpenAI
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500, json={"error": {"message": "private body sk-secret", "type": "server_error"}})

    real_constructor = OpenAI
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        monkeypatch.setattr("app.llm.openai_compatible.OpenAI", lambda **kwargs: real_constructor(http_client=transport, **kwargs))
        client = OpenAICompatibleClient("synthetic-test-key", "https://provider.example/v1", "test-model", "test")
        with pytest.raises(ModelCallError):
            client.chat_json("private system", "private user")
    assert len(calls) == len(logs) == 1
    assert rows(logs)[0].success is False and rows(logs)[0].cost_status == "failed"
    assert "sk-secret" not in str(serialize_model_run(rows(logs)[0]))


def test_known_usage_and_versioned_price_produce_decimal_estimate_without_invoice_claim(logs, monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PRICING_JSON", price_config())
    client, _ = client_with_results([completion("ok")])
    client.chat("system", "user")
    row = rows(logs)[0]
    assert row.estimated_cost == Decimal("0.000040500000")
    output = serialize_model_run(row)
    assert output["estimated_cost"] == "0.000040500000"
    assert output["cost_currency"] == "CNY" and output["pricing_version"] == "synthetic-2026-10"
    assert output["cost_status"] == "estimated" and output["cost_is_invoice"] is False
    assert output["cost_basis"] == "configured_token_estimate"


@pytest.mark.parametrize("usage", [
    None,
    SimpleNamespace(prompt_tokens=None, completion_tokens=0, total_tokens=None),
    SimpleNamespace(prompt_tokens=True, completion_tokens=2, total_tokens=3),
    SimpleNamespace(prompt_tokens=-1, completion_tokens=2, total_tokens=1),
    SimpleNamespace(prompt_tokens="17", completion_tokens=2, total_tokens=None),
])
def test_missing_or_invalid_usage_never_becomes_zero_cost(logs, monkeypatch, usage):
    monkeypatch.setattr(settings, "MODEL_PRICING_JSON", price_config())
    response = completion("ok")
    response.usage = usage
    client, _ = client_with_results([response])
    client.chat("system", "user")
    row = rows(logs)[0]
    assert row.prompt_tokens is None
    assert row.estimated_cost is None and row.cost_status == "missing_usage"


@pytest.mark.parametrize("rates,tokens", [
    ({"input_per_million": "0", "output_per_million": "0"}, (17, 5)),
    ({}, (0, 0)),
])
def test_explicitly_known_zero_is_retained(logs, monkeypatch, rates, tokens):
    monkeypatch.setattr(settings, "MODEL_PRICING_JSON", price_config(**rates))
    response = completion("ok")
    response.usage = {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "total_tokens": sum(tokens)}
    client, _ = client_with_results([response])
    client.chat("system", "user")
    row = rows(logs)[0]
    assert row.estimated_cost == Decimal("0") and row.cost_status == "estimated"
    assert row.prompt_tokens == tokens[0] and row.completion_tokens == tokens[1]


@pytest.mark.parametrize("config,status", [
    ("[]", "unpriced"),
    (price_config(provider="another-provider"), "unpriced"),
    (price_config(model="other-model"), "unpriced"),
    ("private-invalid-json", "invalid_pricing"),
    (price_config(input_per_million="-1"), "invalid_pricing"),
    (price_config(input_per_million="NaN"), "invalid_pricing"),
    (price_config(output_per_million=True), "invalid_pricing"),
    (price_config(currency="unknown"), "invalid_pricing"),
    (json.dumps(json.loads(price_config()) * 2), "invalid_pricing"),
])
def test_price_selection_is_exact_and_invalid_config_is_not_exposed(logs, monkeypatch, config, status):
    monkeypatch.setattr(settings, "MODEL_PRICING_JSON", config)
    client, _ = client_with_results([completion("ok")])
    client.chat("system", "user")
    row = rows(logs)[0]
    assert row.estimated_cost is None and row.cost_status == status
    assert "private-invalid-json" not in str(serialize_model_run(row))


def test_failed_request_has_no_estimated_cost_even_with_usage_or_price(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PRICING_JSON", price_config())
    result = estimate_model_cost("test", "test-model", False, 17, 5)
    assert result == {"estimated_cost": None, "cost_currency": None, "pricing_version": None, "cost_status": "failed"}


@pytest.mark.parametrize("rate,tokens,expected_status", [
    ("1e-10000", 17, "invalid_pricing"),
    ("1e-10000000", 17, "invalid_pricing"),
    ("1e-999999999", 1, "invalid_pricing"),
    ("1000000000", 2_147_483_647, "invalid_pricing"),
    ("1.00000000000000000000000000000000000000000001", 10 ** 100, "missing_usage"),
])
def test_extreme_decimal_configuration_or_usage_does_not_change_successful_business_result(logs, monkeypatch, rate, tokens, expected_status):
    monkeypatch.setattr(settings, "MODEL_PRICING_JSON", price_config(input_per_million=rate, output_per_million="0"))
    response = completion("successful synthetic response")
    response.usage = SimpleNamespace(prompt_tokens=tokens, completion_tokens=0, total_tokens=tokens)
    client, calls = client_with_results([response])
    assert client.chat("system", "user") == "successful synthetic response"
    assert len(calls) == len(logs) == 1
    row = rows(logs)[0]
    assert row.success and row.estimated_cost is None and row.cost_status == expected_status
    if tokens > 2_147_483_647:
        assert row.prompt_tokens is row.total_tokens is None


def test_unexpected_estimator_error_is_redacted_and_never_breaks_response(logs, monkeypatch):
    def fail(*args, **kwargs):
        raise InvalidOperation("private pricing config")
    from decimal import InvalidOperation
    monkeypatch.setattr("app.llm.tracing.estimate_model_cost", fail)
    client, _ = client_with_results([completion("ok")])
    assert client.chat("system", "user") == "ok"
    row = rows(logs)[0]
    assert row.success and row.estimated_cost is None and row.cost_status == "invalid_pricing"
    assert "private" not in str(serialize_model_run(row))


def test_local_rules_are_traced_without_inventing_tokens_or_external_cost(logs):
    with model_trace_context(7, 8, "score_topic", 2):
        LocalRuleBasedClient().chat_json("选题评分", "private user material")
    row = rows(logs)[0]
    assert row.agent_run_id == 7 and row.agent_step_id == 8 and row.workflow_attempt == 2
    assert row.call_kind == row.cost_status == "local_rule" and row.provider == "local"
    assert row.prompt_version == hashlib.sha256("选题评分".encode()).hexdigest()
    assert row.prompt_tokens is row.completion_tokens is row.total_tokens is row.estimated_cost is None
    assert "private" not in str(serialize_model_run(row))


def test_local_rule_failure_keeps_only_error_type_and_no_external_charge(logs, monkeypatch):
    def fail(*args):
        raise ValueError("private local source text")
    monkeypatch.setattr(LocalRuleBasedClient, "_score_topic", fail)
    with model_trace_context(7, 8, "score_topic", 2), pytest.raises(ValueError):
        LocalRuleBasedClient().chat_json("选题评分", "private user material")
    row = rows(logs)[0]
    assert row.success is False and row.error_type == "ValueError"
    assert row.call_kind == row.cost_status == "local_rule" and row.estimated_cost is None
    assert row.error_message is None and "private" not in str(serialize_model_run(row))


def test_serializer_never_returns_legacy_payload_columns_and_keeps_unknown_history_unknown():
    row = ModelRun(task_type="chat", provider="test", model_name="test-model", success=True,
                   input_preview="private input", output_preview="private output", error_message="sk-secret",
                   created_at=datetime(2026, 10, 3))
    output = serialize_model_run(row)
    assert "input_preview" not in output and "output_preview" not in output and "error_message" not in output
    assert "private" not in str(output) and "sk-secret" not in str(output)
    assert output["cost_status"] == "legacy_unknown"
    assert output["estimated_cost"] is None and output["workflow_attempt"] is None
    assert output["created_at"].endswith("+00:00")


OLD_MODEL_TABLE = """
CREATE TABLE model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type VARCHAR(100) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    input_preview TEXT,
    output_preview TEXT,
    success BOOLEAN NOT NULL,
    error_message TEXT,
    latency_ms INTEGER,
    created_at DATETIME
)
"""


def make_legacy_database(path):
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text(OLD_MODEL_TABLE))
        connection.execute(text("INSERT INTO model_runs (task_type, provider, model_name, success) VALUES ('chat','legacy','old-model',1)"))
    return engine


def test_local_legacy_migration_is_repeatable_and_preserves_history(tmp_path):
    engine = make_legacy_database(tmp_path / "legacy.db")
    try:
        _ensure_model_usage_columns(engine)
        _ensure_model_usage_columns(engine)
        names = {column["name"] for column in inspect(engine).get_columns("model_runs")}
        assert {"agent_run_id", "invocation_id", "prompt_version", "estimated_cost", "cost_status"} <= names
        assert {"ix_model_runs_agent_run_id", "ix_model_runs_agent_step_id", "ix_model_runs_invocation_id"} <= {item["name"] for item in inspect(engine).get_indexes("model_runs")}
        with sessionmaker(bind=engine)() as db:
            row = db.query(ModelRun).one()
            assert row.provider == "legacy"
            assert serialize_model_run(row)["cost_status"] == "legacy_unknown"
            assert row.estimated_cost is row.agent_run_id is row.prompt_tokens is None
    finally:
        engine.dispose()


def test_tenant_first_open_migrates_old_logs_and_context_logging_stays_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAS_MODE", "true")
    monkeypatch.setenv("SAAS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_tenant_stores", {})
    identities = ["a" * 32, "b" * 32]
    for identity in identities:
        folder = tmp_path / "tenants" / identity
        folder.mkdir(parents=True)
        engine = make_legacy_database(folder / "content.db")
        engine.dispose()
    try:
        for identity, run_id in zip(identities, (11, 22)):
            token = current_tenant.set(TenantContext(identity, identity, "owner"))
            try:
                with model_trace_context(run_id, run_id + 1, "score_topic", 2):
                    LocalRuleBasedClient().chat("synthetic system", "private scoped user")
            finally:
                current_tenant.reset(token)
        for identity, run_id in zip(identities, (11, 22)):
            with session.tenant_session(identity) as db:
                records = db.query(ModelRun).order_by(ModelRun.id).all()
                assert len(records) == 2
                assert records[0].provider == "legacy" and records[0].agent_run_id is None
                assert records[1].agent_run_id == run_id and records[1].call_kind == "local_rule"
                assert records[1].cost_status == "local_rule"
    finally:
        session.close_tenant_stores()

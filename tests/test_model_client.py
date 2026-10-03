from types import SimpleNamespace

import pytest

from app.llm.openai_compatible import ModelCallError, OpenAICompatibleClient
from app.llm.local import LocalRuleBasedClient


class FakeSession:
    def __init__(self, fail=False):
        self.rows = []
        self.closed = False
        self.rolled_back = False
        self.fail = fail

    def add(self, row):
        self.rows.append(row)

    def commit(self):
        if self.fail:
            raise RuntimeError("database failure")

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture
def logs(monkeypatch):
    from app.db import session
    created = []
    def factory():
        db = FakeSession()
        created.append(db)
        return db
    monkeypatch.setattr(session, "SessionLocal", factory)
    return created


def completion(text, usage=True):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=17, completion_tokens=5, total_tokens=22) if usage else None,
    )


def client_with_results(results):
    pending = iter(results)
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        result = next(pending)
        if isinstance(result, Exception):
            raise result
        return result
    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client.provider = "test"
    client.model = "test-model"
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, calls


def test_call_log_contains_only_hash_timing_and_usage(logs):
    client, calls = client_with_results([completion("private response sk-secret")])
    assert client.chat("private system", "user password=sk-secret") == "private response sk-secret"
    row = logs[0].rows[0]
    assert len(row.prompt_hash) == 64
    assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (17, 5, 22)
    assert row.latency_ms >= 0
    assert row.success
    assert row.input_preview is None and row.output_preview is None and row.error_message is None
    assert row.error_type is None
    assert "private" not in str(row.__dict__) and "sk-secret" not in str(row.__dict__)
    assert logs[0].closed
    assert calls[0]["messages"][1]["content"] == "user password=sk-secret"


def test_role_boundaries_are_part_of_prompt_hash(logs):
    client, _ = client_with_results([completion("a"), completion("b")])
    client.chat("ab", "c")
    client.chat("a", "bc")
    assert logs[0].rows[0].prompt_hash != logs[1].rows[0].prompt_hash


def test_missing_usage_is_unknown_not_zero(logs):
    client, _ = client_with_results([completion("answer", usage=False)])
    client.chat("system", "user")
    row = logs[0].rows[0]
    assert row.prompt_tokens is None and row.completion_tokens is None and row.total_tokens is None


def test_provider_error_is_redacted_in_database_and_outward_exception(logs):
    client, _ = client_with_results([RuntimeError("api_key=sk-secret; private prompt; endpoint?token=abc")])
    with pytest.raises(ModelCallError) as error:
        client.chat("private system", "private user")
    row = logs[0].rows[0]
    assert row.error_type == "RuntimeError"
    assert row.success is False
    assert row.error_message is None
    assert row.input_preview is None and row.output_preview is None
    assert "sk-secret" not in str(error.value) and "private" not in str(row.__dict__)
    assert logs[0].closed


def test_json_response_format_fallback_is_preserved(logs):
    client, calls = client_with_results([
        RuntimeError("response_format json_object not supported; secret must not persist"),
        completion('{"ok": true}'),
    ])
    assert client.chat_json("system", "user") == {"ok": True}
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]
    assert len(calls) == 2
    assert logs[0].rows[0].error_message is None


def test_json_repair_preserves_original_behaviour_without_logging_payload(logs):
    client, calls = client_with_results([completion('{"private": broken}'), completion('{"private": "fixed"}')])
    assert client.chat_json("private system", "private user") == {"private": "fixed"}
    assert len(calls) == 2
    assert calls[1]["temperature"] == 0
    assert "JSON 修复器" in calls[1]["messages"][0]["content"]
    assert all("private" not in str(db.rows[0].__dict__) for db in logs)


def test_api_failure_is_not_sent_to_json_repair(logs):
    client, calls = client_with_results([RuntimeError("Authorization sk-secret was rejected")])
    with pytest.raises(ModelCallError):
        client.chat_json("system", "user")
    assert len(calls) == 1


def test_json_repair_is_bounded_and_final_error_does_not_echo_content(logs):
    client, calls = client_with_results([completion("private invalid output")] * 5)
    with pytest.raises(ValueError) as error:
        client.chat_json("system", "user")
    assert len(calls) == 5
    assert "private" not in str(error.value)


def test_installed_openai_sdk_format_fallback_and_usage(logs):
    import httpx
    from openai import OpenAI
    seen = []
    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(400, json={"error": {
                "message": "response_format json_object not supported",
                "type": "invalid_request_error", "code": "unsupported",
            }})
        return httpx.Response(200, json={
            "id": "test-completion", "object": "chat.completion", "created": 1, "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
        })
    client, _ = client_with_results([])
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        client.client = OpenAI(api_key="test-key", base_url="https://provider.example/v1", http_client=transport, max_retries=0)
        assert client.chat_json("system", "user") == {"ok": True}
    assert logs[0].rows[0].error_type == "BadRequestError"
    assert logs[1].rows[0].total_tokens == 22
    assert all("test-key" not in str(db.rows[0].__dict__) for db in logs)


@pytest.mark.parametrize("value", ['```json\n{"ok": 1}\n```', 'text {"ok": 1} tail'])
def test_fenced_and_embedded_json_compatibility(value):
    assert OpenAICompatibleClient._parse_json_object(value) == {"ok": 1}


def test_logging_failure_rolls_back_and_closes_without_breaking_response(monkeypatch):
    from app.db import session
    db = FakeSession(fail=True)
    monkeypatch.setattr(session, "SessionLocal", lambda: db)
    client, _ = client_with_results([completion("answer")])
    assert client.chat("system", "user") == "answer"
    assert db.closed and db.rolled_back


def test_local_rule_client_does_not_store_prompt_or_output(logs):
    LocalRuleBasedClient().chat("private system", "private password=sk-secret")
    row = logs[0].rows[0]
    assert len(row.prompt_hash) == 64
    assert row.input_preview is None and row.output_preview is None
    assert row.prompt_tokens is None and row.completion_tokens is None and row.total_tokens is None
    assert "private" not in str(row.__dict__) and "sk-secret" not in str(row.__dict__)
    assert logs[0].closed

"""Question-only proposals: bounded rules and offline provider protocol tests.

No fixtures/catalog labels are supplied to the parser. Provider tests substitute
SDK transport/completion only; they are not real-vendor quality acceptance.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from app.agent_core import question_scope as scope
from app.llm.tracing import current_model_trace, model_trace_context, serialize_model_run
from app.schemas.evidence import required_facts_adapter

from test_model_client import client_with_results, completion, logs


QUERY = "合成远山 RX-17的额定电压和售价是多少？"


def pairs(result):
    return [(candidate["product_model"], candidate["parameter"]) for candidate in result["candidates"]]


def check_spans(query, result):
    assert result["query_hash"] == hashlib.sha256(query.encode()).hexdigest()
    assert result["requires_confirmation"] is True
    assert result["requirements_complete"] is False
    assert result["offset_unit"] == "unicode_codepoint"
    for candidate in result["candidates"]:
        for field, key in (("product_model", "product_span"), ("parameter", "parameter_span")):
            span = candidate[key]
            assert query[span["start"]:span["end"]] == span["text"] == candidate[field]
    for issue in result["issues"]:
        span = issue["span"]
        assert 0 <= span["start"] < span["end"] <= len(query)
        assert query[span["start"]:span["end"]] == span["text"]


def payload(query=QUERY):
    result = scope.propose_question_scope(query)
    return {"candidates": result["candidates"], "issues": result["issues"]}


def model(monkeypatch, result):
    client, calls = client_with_results([completion(json.dumps(result, ensure_ascii=False))] * (5 if isinstance(result, list) else 1))
    client.provider = "deepseek"
    monkeypatch.setattr(scope.llm_router, "get_task_client", lambda *a, **kw: client)
    return client, calls


def test_rules_unknown_model_and_missing_catalog_parameter_remain_original_without_model_or_database(monkeypatch, logs):
    monkeypatch.setattr(scope.llm_router, "get_task_client", lambda *a, **kw: pytest.fail("rules must not route to a model"))
    result = scope.propose_question_scope(QUERY)
    assert pairs(result) == [("合成远山 RX-17", "额定电压"), ("合成远山 RX-17", "售价")]
    assert result["method"] == "rules" and result["parser_version"] == "rules_v1"
    assert result["provider"] is result["model"] is None
    assert result["status"] == "proposed" and result["issues"] == []
    assert logs == []
    check_spans(QUERY, result)


@pytest.mark.parametrize("query,expected", [
    ("型号：从未登记的产品；参数：响应时间、报价", [("从未登记的产品", "响应时间"), ("从未登记的产品", "报价")]),
    ("  型号：🧪远山 RX-17 ；参数： 额定电压 、售价  \n", [("🧪远山 RX-17", "额定电压"), ("🧪远山 RX-17", "售价")]),
    ("请问远山 RX-17的电压是什么？远山 RX-22的功率为多少？", [("远山 RX-17", "电压"), ("远山 RX-22", "功率")]),
    ("型号：RX-17；参数：电流 mA、电流 MA", [("RX-17", "电流 mA"), ("RX-17", "电流 MA")]),
    ("型号：RX-17；参数：电流、电流", [("RX-17", "电流")]),
    ("型号：ＲＸ－１７；参数：额定电压\n型号：RX-17；参数：额定 电压", [("ＲＸ－１７", "额定电压")]),
])
def test_supported_rules_keep_exact_spelling_offsets_and_case(query, expected):
    result = scope.propose_question_scope(query)
    assert pairs(result) == expected and result["status"] == "proposed"
    check_spans(query, result)
    required_facts_adapter.validate_python([{key: row[key] for key in ("product_model", "parameter")} for row in result["candidates"]])


@pytest.mark.parametrize("query,code", [
    ("不是 RX-17，是 RX-22，电压是多少？", "negation"),
    ("不要售价，只问远山 RX-17的电压是多少？", "negation"),
    ("远山 RX-17的参数里除功率外，电流是多少？", "negation"),
    ("遠山 RX-17的这个参数是多少？", "ambiguous_binding"),
    ("型号：远山 RX-17；参数：功率或单价", "ambiguous_binding"),
    ("型号：远山 RX-17；参数：或门电压", "ambiguous_binding"),
    ("它的额定电压是多少？", "ambiguous_binding"),
    ("RX-17还是RX-22的电压是多少？", "ambiguous_binding"),
    ("RX-17和RX-22的电压是多少？", "ambiguous_binding"),
    ("型号：甲和乙；参数：电压", "ambiguous_binding"),
    ("型号：；参数：电压", "missing_product"),
    ("型号：RX-17；参数：", "missing_parameter"),
    ("远山 RX-17的", "missing_parameter"),
    ("这台泵的电压是多少？", "missing_product"),
    ("介绍一下这台设备", "unsupported_question"),
    ("RX-17的电压和RX-22的功率是多少？", "ambiguous_binding"),
    ("型号：RX-17；参数：" + "、".join(f"参数{i}" for i in range(11)), "too_many_requirements"),
])
def test_rules_unbound_negated_and_over_limit_questions_require_clarification(query, code):
    result = scope.propose_question_scope(query)
    assert result["status"] == "needs_clarification"
    assert result["candidates"] == []
    assert code in {issue["code"] for issue in result["issues"]}
    check_spans(query, result)


def test_partially_supported_question_preserves_unresolved_span_and_never_claims_complete():
    query = "RX-17的电压是多少？还想了解安装事项。"
    result = scope.propose_question_scope(query)
    assert pairs(result) == [("RX-17", "电压")]
    assert result["status"] == "needs_clarification"
    assert result["issues"][0]["span"]["text"] == "还想了解安装事项"
    check_spans(query, result)


@pytest.mark.parametrize("query,kwargs", [
    ("", {}), ("  ", {}), ("x" * 1001, {}), (None, {}), ("\ud800", {}),
    (QUERY, {"method": "guess"}), (QUERY, {"provider": "deepseek"}),
    (QUERY, {"model": "arbitrary"}), (QUERY, {"model": False}),
    (QUERY, {"method": "model"}), (QUERY, {"method": "model", "provider": "local"}),
    (QUERY, {"method": "model", "provider": "unknown"}),
])
def test_invalid_configuration_fails_before_any_model(query, kwargs, monkeypatch):
    monkeypatch.setattr(scope.llm_router, "get_task_client", lambda *a, **kw: pytest.fail("invalid input routed"))
    with pytest.raises(ValueError):
        scope.propose_question_scope(query, **kwargs)


def test_empty_model_compatible_with_rules():
    assert scope.propose_question_scope(QUERY, model="")["status"] == "proposed"


def test_provider_uses_question_and_schema_only_and_traces_real_adapter_without_agent_fabrication(monkeypatch, logs):
    _, calls = model(monkeypatch, payload())
    with model_trace_context(7, 8, "parent", 3):
        result = scope.propose_question_scope(QUERY, method="model", provider="deepseek", model="")
        assert current_model_trace.get().agent_run_id == 7
    assert current_model_trace.get() is None
    assert result["status"] == "proposed" and result["model"] == "test-model"
    assert result["parser_version"] == "model_structured_v1"
    sent = json.loads(calls[0]["messages"][1]["content"].split("\n\n请严格输出")[0])
    assert set(sent) == {"question", "output_schema"} and sent["question"] == QUERY
    assert calls[0]["temperature"] == 0
    row = logs[0].rows[0]
    assert row.step_key == "question_scope"
    assert row.agent_run_id is row.agent_step_id is row.workflow_attempt is None
    assert row.call_kind == "initial" and row.request_index == 1
    assert row.prompt_version == hashlib.sha256(calls[0]["messages"][0]["content"].encode()).hexdigest()
    assert QUERY not in json.dumps(serialize_model_run(row), ensure_ascii=False)
    check_spans(QUERY, result)


@pytest.mark.parametrize("mutation", [
    "extra_top", "extra_candidate", "missing_issues", "wrong_value", "invented_span", "negative_start",
    "bool_start", "string_start", "float_end", "out_of_bounds", "reverse", "overlap",
    "long_label", "duplicate", "unknown_issue", "invalid_issue_span", "empty_issue_span", "too_many", "non_object",
])
def test_provider_shape_and_raw_spans_reject_whole_proposal_without_additional_semantic_retry(monkeypatch, logs, mutation):
    value = payload()
    candidate = value["candidates"][0]
    if mutation == "extra_top": value["answer"] = "private invented answer"
    elif mutation == "extra_candidate": candidate["value"] = "480 V"
    elif mutation == "missing_issues": del value["issues"]
    elif mutation == "wrong_value": candidate["product_model"] = "canonical RX-17"
    elif mutation == "invented_span": candidate["parameter_span"]["text"] = "invented"
    elif mutation == "negative_start": candidate["product_span"]["start"] = -1
    elif mutation == "bool_start": candidate["product_span"]["start"] = False
    elif mutation == "string_start": candidate["product_span"]["start"] = "0"
    elif mutation == "float_end": candidate["product_span"]["end"] = 12.0
    elif mutation == "out_of_bounds": candidate["product_span"]["end"] = 1001
    elif mutation == "reverse": candidate["product_span"]["end"] = candidate["product_span"]["start"]
    elif mutation == "overlap": candidate["parameter"] = candidate["product_model"]; candidate["parameter_span"] = deepcopy(candidate["product_span"])
    elif mutation == "long_label": candidate["parameter"] = "X" * 201
    elif mutation == "duplicate": value["candidates"].append(deepcopy(candidate))
    elif mutation == "unknown_issue": value["issues"] = [{"code": "private model reasoning", "span": candidate["product_span"]}]
    elif mutation == "invalid_issue_span": value["issues"] = [{"code": "negation", "span": {"start": 0, "end": 4, "text": "wrong"}}]
    elif mutation == "empty_issue_span": value["issues"] = [{"code": "negation", "span": {"start": 0, "end": 0, "text": ""}}]
    elif mutation == "too_many": value["candidates"] = [deepcopy(candidate)] * 11
    else: value = ["private provider payload"]
    _, calls = model(monkeypatch, value)
    with pytest.raises(scope.QuestionScopeError) as error:
        scope.propose_question_scope(QUERY, method="model", provider="deepseek")
    assert error.value.code == "invalid_scope_proposal" and error.value.status_code == 502
    assert "private" not in str(error.value)
    # Non-object JSON uses the pre-existing adapter's bounded JSON retries.
    assert len(calls) == (5 if mutation == "non_object" else 1)
    assert current_model_trace.get() is None


def test_model_cannot_override_finite_negation_guard(monkeypatch, logs):
    query = QUERY + "不要售价"
    value = payload()  # All offsets remain valid, but the sentence now excludes a requirement.
    model(monkeypatch, value)
    result = scope.propose_question_scope(query, method="model", provider="deepseek")
    assert result["candidates"] == [] and result["status"] == "needs_clarification"
    assert result["issues"][0]["code"] == "negation"
    check_spans(query, result)


def test_empty_model_proposal_means_unassessed_clarification(monkeypatch, logs):
    model(monkeypatch, {"candidates": [], "issues": []})
    result = scope.propose_question_scope(QUERY, method="model", provider="deepseek")
    assert result["status"] == "needs_clarification" and result["issues"][0]["code"] == "unsupported_question"


@pytest.mark.parametrize("responses,kinds", [
    ([RuntimeError("response_format unsupported private body")], ["initial", "format_fallback"]),
    ([completion("invalid private json")], ["initial", "json_repair"]),
])
def test_existing_protocol_retries_are_counted_in_one_invocation(monkeypatch, logs, responses, kinds):
    client, calls = client_with_results(responses + [completion(json.dumps(payload(), ensure_ascii=False))])
    client.provider = "deepseek"
    monkeypatch.setattr(scope.llm_router, "get_task_client", lambda *a, **kw: client)
    assert scope.propose_question_scope(QUERY, method="model", provider="deepseek")["status"] == "proposed"
    rows = [entry.rows[0] for entry in logs]
    assert len(calls) == len(rows) == 2
    assert len({row.invocation_id for row in rows}) == 1
    assert [row.request_index for row in rows] == [1, 2]
    assert [row.call_kind for row in rows] == kinds
    assert all(row.step_key == "question_scope" and row.workflow_attempt is None for row in rows)


def test_provider_failure_is_sanitized_no_rules_fallback(monkeypatch, logs):
    client, calls = client_with_results([RuntimeError("private API key sk-test secret endpoint")])
    client.provider = "deepseek"
    monkeypatch.setattr(scope.llm_router, "get_task_client", lambda *a, **kw: client)
    monkeypatch.setattr(scope, "_rules", lambda query: pytest.fail("no automatic fallback"))
    with pytest.raises(scope.QuestionScopeError) as error:
        scope.propose_question_scope(QUERY, method="model", provider="deepseek")
    assert error.value.code == "scope_provider_failed" and error.value.status_code == 503
    assert len(calls) == 1 and logs[0].rows[0].success is False
    assert "private" not in str(error.value) and "sk-test" not in json.dumps(serialize_model_run(logs[0].rows[0]))
    assert current_model_trace.get() is None


def test_router_configuration_error_is_fixed_and_does_not_expose_exception(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("private key and endpoint")
    monkeypatch.setattr(scope.llm_router, "get_task_client", unavailable)
    with pytest.raises(ValueError, match="范围提案模型配置不可用") as error:
        scope.propose_question_scope(QUERY, method="model", provider="deepseek")
    assert "private" not in str(error.value)


def test_installed_sdk_protocol_uses_only_mock_transport_and_no_hidden_retry(monkeypatch, logs):
    import httpx
    from openai import OpenAI
    from app.llm.openai_compatible import OpenAICompatibleClient
    requests = []
    def reply(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 1,
            "model": "synthetic-scope", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(payload(), ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 24, "total_tokens": 36}})
    with httpx.Client(transport=httpx.MockTransport(reply)) as transport:
        constructor = OpenAI
        monkeypatch.setattr("app.llm.openai_compatible.OpenAI", lambda **kw: constructor(http_client=transport, **kw))
        client = OpenAICompatibleClient("synthetic-test-only", "https://synthetic.invalid/v1", "synthetic-scope", "deepseek")
        monkeypatch.setattr(scope.llm_router, "get_task_client", lambda *a, **kw: client)
        assert scope.propose_question_scope(QUERY, method="model", provider="deepseek")["model"] == "synthetic-scope"
        assert client.client.max_retries == 0
    assert len(requests) == len(logs) == 1
    assert logs[0].rows[0].total_tokens == 36

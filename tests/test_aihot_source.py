"""Offline AIHot adapter contracts; every article and response is synthetic."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest
from pydantic import ValidationError

from app.services import aihot_source as aihot


class Clock:
    def __init__(self):
        self.value = 1_800_000_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def article(identifier="synthetic-1", **extra):
    item = {
        "id": identifier,
        "title": "合成测试资料，不是真实新闻",
        "summary": "用于验证只读来源适配器的数据格式与缓存行为。",
        "source": {"name": "Synthetic Independent Publisher"},
        "links": {
            "aihot": "https://aihot.news/items/synthetic-1",
            "original": "https://publisher.example/articles/synthetic-1",
        },
    }
    item.update(extra)
    return item


def payload(items=None, **extra):
    rows = [article()] if items is None else items
    result = {
        "schemaVersion": 1,
        "items": rows,
        "page": {"count": len(rows), "hasMore": False, "nextCursor": None},
    }
    result.update(extra)
    return result


def response(status=200, *, body=None, headers=None):
    options = {} if status == 304 else {"json": payload() if body is None else body}
    return httpx.Response(
        status,
        headers=headers or {},
        request=httpx.Request("GET", "https://aihot.news/api/v1/items"),
        **options,
    )


def scripted_source(monkeypatch, responses):
    clock = Clock()
    source = aihot.AihotSource(now=clock)
    calls = []
    pending = iter(responses)

    async def fetch(params, etag):
        calls.append({"params": deepcopy(params), "etag": etag})
        result = next(pending)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(aihot, "_fetch", fetch)
    return source, clock, calls


def read(source, query=None, *, enabled=True):
    return asyncio.run(source.list_items(query or aihot.AihotQuery(), enabled=enabled))


def test_query_normalizes_whitespace_and_accepts_documented_categories():
    query = aihot.AihotQuery(q="  合成关键词  ", limit=30)
    assert query.q == "合成关键词"
    assert aihot.AihotQuery(q="   ").q == ""
    for category in ("ai-models", "ai-products", "industry", "paper", "tip"):
        assert aihot.AihotQuery(category=category).category == category


@pytest.mark.parametrize("values", [
    {"q": "一"}, {"q": "词" * 201}, {"category": "invented-category"},
    {"limit": 0}, {"limit": 31}, {"mode": "invented-mode"}, {"window": "invented-window"},
])
def test_invalid_queries_are_rejected_before_fetch(values):
    with pytest.raises(ValidationError):
        aihot.AihotQuery(**values)


def test_disabled_source_never_fetches_or_returns_cached_articles(monkeypatch):
    source, _, calls = scripted_source(monkeypatch, [response()])
    disabled = read(source, enabled=False)
    assert disabled["refresh"]["status"] == "disabled"
    assert disabled["items"] == []
    assert calls == []
    assert read(source)["items"]
    disabled_again = read(source, enabled=False)
    assert disabled_again["refresh"]["status"] == "disabled"
    assert disabled_again["items"] == []
    assert len(calls) == 1


@pytest.mark.parametrize("cache_control,ttl", [("s-maxage=5", 60), ("s-maxage=120", 120)])
def test_success_cache_respects_minimum_ttl_and_server_s_maxage(monkeypatch, cache_control, ttl):
    source, clock, calls = scripted_source(monkeypatch, [
        response(headers={"Cache-Control": cache_control}),
        response(body=payload([article("synthetic-2")])),
    ])
    first = read(source)
    assert first["refresh"]["status"] == "ready"
    assert not first["refresh"]["stale"]
    assert not first["refresh"]["from_cache"]
    clock.advance(ttl - 1)
    cached = read(source)
    assert cached["items"] == first["items"]
    assert cached["refresh"]["from_cache"]
    assert len(calls) == 1
    clock.advance(2)
    refreshed = read(source)
    assert refreshed["items"][0]["id"] == "synthetic-2"
    assert len(calls) == 2


def test_expired_entry_sends_etag_and_304_reuses_items(monkeypatch):
    source, clock, calls = scripted_source(monkeypatch, [
        response(headers={"ETag": '"synthetic-v1"', "Cache-Control": "s-maxage=60"}),
        response(304, headers={"Cache-Control": "s-maxage=60"}),
    ])
    first = read(source)
    clock.advance(61)
    refreshed = read(source)
    assert calls[0]["etag"] in (None, "")
    assert calls[1]["etag"] == '"synthetic-v1"'
    assert refreshed["items"] == first["items"]
    assert refreshed["refresh"]["status"] == "ready"
    assert not refreshed["refresh"]["stale"]
    assert refreshed["refresh"]["last_success_at"] != first["refresh"]["last_success_at"]
    assert read(source)["items"] == first["items"]
    assert len(calls) == 2


def test_cache_keys_are_isolated_and_eviction_is_lru(monkeypatch):
    source, _, calls = scripted_source(monkeypatch, [
        response(body=payload([article(f"synthetic-{index}")])) for index in range(18)
    ])
    queries = [aihot.AihotQuery(q=f"synthetic query {index}") for index in range(17)]
    initial = [read(source, query) for query in queries[:16]]
    assert initial[0]["items"] != initial[1]["items"]
    assert read(source, queries[0])["items"] == initial[0]["items"]
    assert len(calls) == 16
    read(source, queries[16])
    assert read(source, queries[0])["items"] == initial[0]["items"]
    assert len(calls) == 17
    evicted = read(source, queries[1])
    assert len(calls) == 18
    assert evicted["items"] != initial[1]["items"]


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_backs_off_all_queries_without_sharing_cached_data(monkeypatch, status):
    source, clock, calls = scripted_source(monkeypatch, [
        response(headers={"Cache-Control": "s-maxage=60"}),
        response(status, headers={"Retry-After": "120"}),
        response(body=payload([article("synthetic-other")])),
    ])
    original = read(source)
    clock.advance(61)
    failed = read(source)
    assert failed["refresh"]["status"] == "error"
    assert failed["refresh"]["stale"]
    assert failed["items"] == original["items"]
    other_query = aihot.AihotQuery(q="another synthetic query")
    other = read(source, other_query)
    assert other["refresh"]["status"] == "error"
    assert other["items"] == []
    assert len(calls) == 2
    clock.advance(119)
    assert read(source, other_query)["items"] == []
    assert len(calls) == 2
    clock.advance(2)
    recovered = read(source, other_query)
    assert recovered["refresh"]["status"] == "ready"
    assert recovered["items"][0]["id"] == "synthetic-other"
    assert len(calls) == 3


def test_stale_items_expire_after_five_extra_minutes(monkeypatch):
    source, clock, calls = scripted_source(monkeypatch, [
        response(headers={"Cache-Control": "s-maxage=60"}),
        response(503, headers={"Retry-After": "600"}),
    ])
    original = read(source)
    clock.advance(61)
    stale = read(source)
    assert stale["items"] == original["items"]
    assert stale["refresh"]["last_success_at"] == original["refresh"]["last_success_at"]
    clock.advance(300)
    expired = read(source)
    assert expired["items"] == []
    assert expired["refresh"]["status"] == "error"
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 500])
def test_nontransient_http_failure_discards_old_articles(monkeypatch, status):
    source, clock, _ = scripted_source(monkeypatch, [response(), response(status)])
    assert read(source)["items"]
    clock.advance(61)
    failed = read(source)
    assert failed["refresh"]["status"] == "error"
    assert failed["items"] == []
    assert not failed["refresh"]["stale"]
    assert read(source)["items"] == []


def test_403_clears_other_query_caches_and_enforces_provider_cooldown(monkeypatch):
    source, clock, calls = scripted_source(monkeypatch, [
        response(headers={"Cache-Control": "s-maxage=60", "ETag": '"synthetic-a"'}),
        response(body=payload([article("synthetic-b")]), headers={
            "Cache-Control": "s-maxage=300", "ETag": '"synthetic-b"',
        }),
        response(403),
        response(body=payload([article("synthetic-recovered")])),
    ])
    query_b = aihot.AihotQuery(q="synthetic query b")
    assert read(source)["items"]
    assert read(source, query_b)["items"][0]["id"] == "synthetic-b"
    clock.advance(61)
    forbidden = read(source)
    assert forbidden["refresh"]["status"] == "error"
    assert forbidden["items"] == []
    for query in (query_b, aihot.AihotQuery(q="new synthetic query")):
        blocked = read(source, query)
        assert blocked["refresh"]["status"] == "error"
        assert blocked["items"] == []
        assert not blocked["refresh"]["stale"]
    assert len(calls) == 3
    clock.advance(61)
    recovered = read(source, query_b)
    assert recovered["refresh"]["status"] == "ready"
    assert recovered["items"][0]["id"] == "synthetic-recovered"
    assert calls[-1]["etag"] in (None, "")
    assert len(calls) == 4


def test_network_failure_preserves_short_lived_stale_items(monkeypatch):
    source, clock, _ = scripted_source(monkeypatch, [response(), httpx.ConnectError("synthetic outage")])
    original = read(source)
    clock.advance(61)
    failed = read(source)
    assert failed["refresh"]["status"] == "error"
    assert failed["refresh"]["stale"]
    assert failed["items"] == original["items"]


def test_no_store_keeps_cooldown_without_retaining_body_or_etag(monkeypatch):
    source, clock, calls = scripted_source(monkeypatch, [
        response(headers={"Cache-Control": "no-store", "ETag": '"must-not-store"'}),
        response(body=payload([article("synthetic-new")])),
    ])
    first = read(source)
    assert first["refresh"]["status"] == "ready"
    assert first["items"][0]["id"] == "synthetic-1"
    waiting = read(source)
    assert waiting["refresh"]["status"] == "error"
    assert waiting["items"] == []
    assert "等待" in waiting["refresh"]["error"]
    assert len(calls) == 1
    clock.advance(61)
    refreshed = read(source)
    assert refreshed["items"][0]["id"] == "synthetic-new"
    assert len(calls) == 2
    assert calls[1]["etag"] in (None, "")


@pytest.mark.parametrize("cache_control,cooldown", [("no-cache", 60), ("no-cache, s-maxage=600", 600)])
def test_no_cache_hides_unvalidated_body_during_cooldown_then_revalidates(monkeypatch, cache_control, cooldown):
    source, clock, calls = scripted_source(monkeypatch, [
        response(headers={"Cache-Control": cache_control, "ETag": '"revalidate"'}),
        response(304, headers={"Cache-Control": "no-cache"}),
    ])
    first = read(source)
    waiting = read(source)
    assert waiting["refresh"]["status"] == "error"
    assert waiting["items"] == []
    assert "等待" in waiting["refresh"]["error"]
    assert len(calls) == 1
    clock.advance(cooldown + 1)
    refreshed = read(source)
    assert refreshed["items"] == first["items"]
    assert refreshed["refresh"]["status"] == "ready"
    assert len(calls) == 2
    assert calls[1]["etag"] == '"revalidate"'


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("https://private.invalid/?api_key=synthetic-secret"),
    httpx.ReadTimeout("synthetic-secret from private upstream"),
])
def test_failure_without_cache_returns_empty_redacted_error(monkeypatch, failure):
    source, _, calls = scripted_source(monkeypatch, [failure])
    result = read(source)
    assert result["refresh"]["status"] == "error"
    assert result["items"] == []
    assert result["refresh"]["error"]
    assert "synthetic-secret" not in str(result)
    assert "private.invalid" not in str(result)
    assert len(calls) == 1


def test_unknown_safe_metadata_and_rights_survive_without_official_verification(monkeypatch):
    extra = {
        "category": "future-synthetic-category",
        "canonical": {"url": "https://publisher.example/canonical"},
        "rights": {"license": "synthetic-license", "url": "https://publisher.example/rights"},
        "attribution": {"name": "Synthetic author", "url": "https://publisher.example/author"},
        "futureMetadata": {"labels": ["synthetic", "not-real-news"], "confidence": 0.42},
    }
    source, _, _ = scripted_source(monkeypatch, [response(body=payload([article(**extra)]))])
    item = read(source)["items"][0]
    for name, value in extra.items():
        assert item[name] == value
    assert item["source"]["name"] == "Synthetic Independent Publisher"
    assert item["verification_status"] == "unverified"
    assert item["content_kind"] == "ai_summary"


def test_unsafe_urls_are_null_while_safe_links_and_plain_text_survive(monkeypatch):
    item = article(
        links={
            "aihot": "https://aihot.news/items/synthetic-1",
            "original": "javascript:alert(1)",
            "futureLink": "file:///etc/passwd",
            "private": "http://127.0.0.1/private",
        },
        attribution={"name": "Synthetic author", "url": "https://user:secret@publisher.example/"},
        futureMetadata={"url": "data:text/html,synthetic", "label": "plain text remains"},
    )
    source, _, _ = scripted_source(monkeypatch, [response(body=payload([item]))])
    cleaned = read(source)["items"][0]
    assert cleaned["links"]["aihot"] == item["links"]["aihot"]
    for key in ("original", "futureLink", "private"):
        assert cleaned["links"][key] is None
    assert cleaned["attribution"]["url"] is None
    assert cleaned["futureMetadata"]["url"] is None
    assert cleaned["futureMetadata"]["label"] == "plain text remains"


@pytest.mark.parametrize("body", [
    {"items": [], "page": {}, "schemaVersion": 999},
    {"items": "not-a-list", "page": {}},
    {"items": []},
    {"page": {}},
    payload(query={"mode": "selected", "window": "24h", "q": "different query"}),
])
def test_invalid_upstream_envelope_cannot_be_reported_as_ready(monkeypatch, body):
    source, _, _ = scripted_source(monkeypatch, [response(body=body)])
    result = read(source)
    assert result["refresh"]["status"] == "error"
    assert result["items"] == []


def test_invalid_payload_replaces_neither_error_state_with_ready_nor_stale_data(monkeypatch):
    source, clock, _ = scripted_source(monkeypatch, [response(), response(body={"items": []})])
    assert read(source)["items"]
    clock.advance(61)
    result = read(source)
    assert result["refresh"]["status"] == "error"
    assert result["items"] == []
    assert not result["refresh"]["stale"]


@pytest.mark.parametrize("section,field,value", [
    pytest.param("source", "name", {}, id="source-name-object"),
    pytest.param("source", "name", None, id="source-name-null"),
    pytest.param("item", "summary", {}, id="summary-object"),
    pytest.param("item", "reason", {}, id="reason-object"),
    pytest.param("item", "originalTitle", {}, id="original-title-object"),
    pytest.param("item", "publishedAt", {}, id="published-at-object"),
    pytest.param("item", "publishedAt", 1234, id="published-at-number"),
    pytest.param("item", "discoveredAt", {}, id="discovered-at-object"),
    pytest.param("item", "discoveredAt", 1234, id="discovered-at-number"),
    pytest.param("item", "score", float("nan"), id="score-nan"),
    pytest.param("item", "score", float("inf"), id="score-infinity"),
    pytest.param("item", "score", float("-inf"), id="score-negative-infinity"),
    pytest.param("item", "score", True, id="score-bool"),
    pytest.param("item", "score", "0.8", id="score-string"),
    pytest.param("item", "selected", "true", id="selected-string"),
    pytest.param("item", "selected", 1, id="selected-number"),
    pytest.param("item", "selected", None, id="selected-null"),
    pytest.param("page", "count", -1, id="page-count-negative"),
    pytest.param("page", "count", True, id="page-count-bool"),
    pytest.param("page", "count", "1", id="page-count-string"),
    pytest.param("page", "count", 1.5, id="page-count-fraction"),
    pytest.param("page", "count", None, id="page-count-null"),
    pytest.param("page", "hasMore", "false", id="page-has-more-string"),
    pytest.param("page", "hasMore", 0, id="page-has-more-number"),
    pytest.param("page", "hasMore", None, id="page-has-more-null"),
    pytest.param("page", "nextCursor", 1234, id="page-next-cursor-number"),
    pytest.param("page", "nextCursor", {}, id="page-next-cursor-object"),
    pytest.param("page", "nextCursor", [], id="page-next-cursor-list"),
    pytest.param("page", "nextCursor", False, id="page-next-cursor-bool"),
])
def test_malformed_known_fields_are_errors_and_discard_old_articles(monkeypatch, section, field, value):
    body = payload()
    target = body["page"] if section == "page" else body["items"][0]
    if section == "source":
        target = target["source"]
    target[field] = value
    # Raw bytes exercise the decoder's handling of NaN/Infinity. HTTPX's json=
    # encoder rejects these before they could reach the adapter under test.
    malformed = httpx.Response(
        200, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
    )
    source, clock, calls = scripted_source(monkeypatch, [response(), malformed])
    assert read(source)["items"]
    clock.advance(61)
    result = read(source)
    assert result["refresh"]["status"] == "error"
    assert result["items"] == []
    assert not result["refresh"]["stale"]
    assert read(source)["items"] == []
    assert len(calls) == 2


@pytest.mark.parametrize("section,field", [
    ("source", "name"), ("page", "count"), ("page", "hasMore"), ("page", "nextCursor"),
])
def test_missing_required_source_and_page_fields_discard_old_articles(monkeypatch, section, field):
    body = payload()
    target = body["page"] if section == "page" else body["items"][0]["source"]
    del target[field]
    source, clock, _ = scripted_source(monkeypatch, [response(), response(body=body)])
    assert read(source)["items"]
    clock.advance(61)
    result = read(source)
    assert result["refresh"]["status"] == "error"
    assert result["items"] == []
    assert not result["refresh"]["stale"]


@pytest.mark.parametrize("nullable", [False, True])
def test_well_typed_optional_fields_remain_supported(monkeypatch, nullable):
    optional = {
        "summary": None if nullable else "合成摘要",
        "reason": None if nullable else "合成选题理由",
        "originalTitle": None if nullable else "Synthetic original title",
        "publishedAt": None if nullable else "2026-09-19T00:00:00Z",
        "discoveredAt": None if nullable else "2026-09-19T00:01:00Z",
        "score": None if nullable else 0.8,
        "selected": False,
    }
    body = payload([article(**optional)])
    body["page"]["hasMore"] = not nullable
    body["page"]["nextCursor"] = None if nullable else "synthetic-cursor"
    source, _, _ = scripted_source(monkeypatch, [response(body=body)])
    result = read(source)
    assert result["refresh"]["status"] == "ready"
    for field, value in optional.items():
        assert result["items"][0][field] == value


def test_empty_valid_response_is_ready_not_provider_failure(monkeypatch):
    source, _, _ = scripted_source(monkeypatch, [response(body=payload([]))])
    result = read(source)
    assert result["refresh"]["status"] == "ready"
    assert result["items"] == []
    assert result["page"]["count"] == 0


def test_transport_is_fixed_read_only_and_disables_redirects_and_environment(monkeypatch):
    original_client = httpx.AsyncClient
    requests, configurations = [], []

    class JsonBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps(payload()).encode()

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=JsonBody())

    def client(**kwargs):
        configurations.append(kwargs)
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(aihot.httpx, "AsyncClient", client)
    result = read(aihot.AihotSource(now=Clock()), aihot.AihotQuery(q=" synthetic query "))
    assert result["refresh"]["status"] == "ready"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "aihot.news"
    assert request.url.path == "/api/v1/items"
    assert request.url.params["q"] == "synthetic query"
    assert request.headers["Accept-Encoding"] == "identity"
    assert configurations[0]["trust_env"] is False
    assert configurations[0]["follow_redirects"] is False
    assert configurations[0]["timeout"] == 12.0


@pytest.mark.parametrize("kind", ["redirect", "declared_oversize", "streamed_oversize"])
def test_transport_rejects_redirects_and_oversized_responses(monkeypatch, kind):
    original_client = httpx.AsyncClient
    requests = []

    class LargeBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * (2 * 1024 * 1024)
            yield b"x"

    def handler(request):
        requests.append(request)
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://elsewhere.example/private"})
        if kind == "declared_oversize":
            return httpx.Response(200, content=b"{}", headers={
                "Content-Type": "application/json", "Content-Length": str(2 * 1024 * 1024 + 1),
            })
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=LargeBody())

    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(aihot.httpx, "AsyncClient", client)
    result = read(aihot.AihotSource(now=Clock()))
    assert result["refresh"]["status"] == "error"
    assert result["items"] == []
    assert len(requests) == 1


def test_disabled_asgi_routes_validate_queries_without_network_or_database(monkeypatch):
    from fastapi import FastAPI
    from app.api.routes import source_hub

    application = FastAPI()
    application.include_router(source_hub.router, prefix="/api")
    monkeypatch.setattr(source_hub.settings, "AIHOT_ENABLED", False)
    monkeypatch.setattr(source_hub, "aihot_source", aihot.AihotSource(now=Clock()))
    calls = []

    async def forbidden_fetch(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("disabled routes must not access the network")

    monkeypatch.setattr(aihot, "_fetch", forbidden_fetch)

    async def run():
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            status = await client.get("/api/source-hub/aihot/status")
            assert status.status_code == 200
            assert status.json()["enabled"] is False
            assert status.json()["read_only"] is True
            assert status.json()["embed_supported"] is False

            listing = await client.get("/api/source-hub/aihot")
            assert listing.status_code == 200
            assert listing.headers["Cache-Control"] == "no-store"
            assert listing.json()["refresh"]["status"] == "disabled"
            assert listing.json()["items"] == []

            for params in (
                {"q": "一"}, {"q": "词" * 201}, {"mode": "unknown"}, {"window": "1y"},
                {"category": "unknown"}, {"limit": 0}, {"limit": 31}, {"limit": "invalid"},
            ):
                invalid = await client.get("/api/source-hub/aihot", params=params)
                assert invalid.status_code == 422, (params, invalid.text)

    asyncio.run(run())
    assert calls == []

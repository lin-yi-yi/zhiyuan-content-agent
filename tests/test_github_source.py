"""Offline GitHub release contracts; all payloads and release text are synthetic."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import format_datetime
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.services import github_source as github


class Clock:
    value = 1_800_000_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def release(identifier=1, project="langchain", **extra):
    value = {
        "id": identifier, "name": "Synthetic release", "tag_name": "v0.0.1-synthetic",
        "draft": False, "prerelease": False, "published_at": "2026-01-02T03:04:05Z",
        "html_url": f"https://github.com/{github.PROJECTS[project]}/releases/tag/synthetic-{identifier}",
        "body": "DO_NOT_COPY_SYNTHETIC_RELEASE_BODY", "assets": [{"secret": "DO_NOT_COPY_ASSET"}],
    }
    value.update(extra)
    return value


def response(status=200, rows=None, headers=None):
    return httpx.Response(status, headers=headers or {},
                          **({"json": [release()] if rows is None else rows} if status == 200 else {}))


def scripted(monkeypatch, responses):
    clock, calls, pending = Clock(), [], iter(responses)
    source = github.GithubSource(now=clock)

    async def fetch(project, etag):
        calls.append((project, etag))
        result = next(pending)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(github, "_fetch", fetch)
    return source, clock, calls


def read(source, project="langchain", limit=5, enabled=True):
    return asyncio.run(source.list_items(github.GithubQuery(project=project, limit=limit), enabled=enabled))


@pytest.mark.parametrize("values", [
    {"project": "https://internal.invalid"}, {"project": "../dify"}, {"limit": 0},
    {"limit": 21}, {"url": "https://example.com"},
])
def test_query_rejects_unbounded_input(values):
    with pytest.raises(ValidationError):
        github.GithubQuery(**values)


def test_disabled_mode_neither_fetches_nor_returns_cached_releases(monkeypatch):
    source, _, calls = scripted(monkeypatch, [response()])
    assert read(source, enabled=False)["refresh"]["status"] == "disabled"
    assert not calls
    assert read(source)["items"]
    assert not read(source, enabled=False)["items"]
    assert len(calls) == 1


def test_release_contract_keeps_metadata_attribution_and_unverified_status(monkeypatch):
    source, clock, _ = scripted(monkeypatch, [response(rows=[
        release(name=None, prerelease=True), release(2, draft=True),
    ], headers={"Link": '<https://api.github.com/ignored>; rel="next"'})])
    result = read(source, limit=1)
    assert result["provider"] == "github"
    assert result["page"] == {"count": 1, "hasMore": True, "nextCursor": None}
    item = result["items"][0]
    assert item["id"] == "github:langchain-ai/langchain:1"
    assert item["title"] == item["version_label"] == "v0.0.1-synthetic"
    assert item["source"] == {"name": "langchain-ai/langchain"}
    assert item["links"]["original"] == item["links"]["github"] == release()["html_url"]
    assert item["publishedAt"] == "2026-01-02T03:04:05Z"
    assert item["discoveredAt"] == github._iso(clock())
    assert item["discovered_at_basis"] == "connector_fetch_time"
    assert item["verification_status"] == "unverified"
    assert item["source_tier"] == "primary"
    assert item["content_kind"] == "maintainer_release"
    assert "预发布" in item["summary"]
    assert "DO_NOT_COPY" not in json.dumps(result)


@pytest.mark.parametrize("rows", [
    {"message": "private details"}, [None], [release(id=True)], [release(id=-1)],
    [release(draft="false")], [release(prerelease=1)], [release(tag_name=" ")],
    [release(name=[])], [release(published_at="not-a-date")],
    [release(published_at="2026-01-02T03:04:05")], [release(), release()],
    [release(html_url="javascript:alert(1)")],
    [release(html_url="https://github.com.evil.invalid/langchain-ai/langchain/releases/tag/x")],
    [release(html_url="https://github.com/langgenius/dify/releases/tag/x")],
    [release(html_url="https://user@github.com/langchain-ai/langchain/releases/tag/x")],
    [release(html_url="https://github.com/langchain-ai/langchain/releases/tag/x?token=secret")],
])
def test_invalid_upstream_data_is_hidden_and_not_cached(monkeypatch, rows):
    source, _, calls = scripted(monkeypatch, [response(rows=rows)])
    result = read(source)
    assert result["items"] == []
    assert result["refresh"]["status"] == "error"
    assert "private details" not in json.dumps(result)
    assert read(source)["items"] == []
    assert len(calls) == 1


def test_cache_is_per_repository_and_limit_only_slices_a_defensive_copy(monkeypatch):
    source, _, calls = scripted(monkeypatch, [
        response(rows=[release(1), release(2)]), response(rows=[release(3, "dify")]),
    ])
    first = read(source, limit=1)
    assert first["page"]["hasMore"]
    first["items"][0]["source"]["name"] = "mutated caller value"
    second = read(source, limit=20)
    assert second["page"]["count"] == 2
    assert second["items"][0]["source"]["name"] == "langchain-ai/langchain"
    assert second["refresh"]["from_cache"]
    assert read(source, project="dify")["items"][0]["id"] == "github:langgenius/dify:3"
    assert len(calls) == 2


def test_expired_cache_revalidates_with_etag_and_retains_original_fetch_time(monkeypatch):
    source, clock, calls = scripted(monkeypatch, [
        response(headers={"ETag": '"synthetic-v1"'}), response(304),
    ])
    first = read(source)
    clock.advance(59)
    assert read(source)["refresh"]["from_cache"]
    assert len(calls) == 1
    clock.advance(1)
    second = read(source)
    assert calls == [("langchain", None), ("langchain", '"synthetic-v1"')]
    assert second["items"] == first["items"]
    assert second["refresh"]["last_success_at"] != first["refresh"]["last_success_at"]


@pytest.mark.parametrize("directive", ["no-store", "no-cache"])
def test_restrictive_cache_control_keeps_cooldown_and_304_inherits_directives(monkeypatch, directive):
    source, clock, calls = scripted(monkeypatch, [
        response(headers={"Cache-Control": directive, "ETag": '"synthetic"'}),
        response(304) if directive == "no-cache" else response(),
    ])
    first = read(source)
    assert first["items"]
    assert not read(source)["items"]
    assert len(calls) == 1
    clock.advance(60)
    second = read(source)
    assert second["items"]
    assert calls[1][1] == ('"synthetic"' if directive == "no-cache" else None)
    if directive == "no-cache":
        assert not read(source)["items"]


@pytest.mark.parametrize("status", [403, 429, 503])
def test_rate_failure_clears_all_caches_and_prevents_project_bypass(monkeypatch, status):
    source, clock, calls = scripted(monkeypatch, [
        response(), response(rows=[release(1, "dify")]),
        response(status, headers={"Retry-After": "120"}), response(rows=[release(2, "dify")]),
    ])
    assert read(source)["items"]
    assert read(source, "dify")["items"]
    clock.advance(60)
    failed = read(source)
    assert failed["items"] == []
    assert failed["refresh"]["next_refresh_at"] == github._iso(clock() + 120)
    for project in github.PROJECTS:
        assert read(source, project)["items"] == []
    assert len(calls) == 3
    clock.advance(119)
    assert not read(source, "dify")["items"]
    assert len(calls) == 3
    clock.advance(1)
    assert read(source, "dify")["items"]
    assert calls[-1] == ("dify", None)


def test_retry_reset_date_and_exponential_backoff():
    now = Clock()()
    retry_date = format_datetime(datetime.fromtimestamp(now + 150, timezone.utc), usegmt=True)
    assert github._backoff_seconds({"retry-after": retry_date}, now, 1) == 150
    assert github._backoff_seconds({"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(now + 300))}, now, 1) == 300
    assert github._backoff_seconds({"retry-after": "invalid"}, now, 1) == 60
    assert github._backoff_seconds({}, now, 2) == 120
    assert github._backoff_seconds({}, now, 100) == 3600


@pytest.mark.parametrize("failure", [response(302), response(304), response(404), RuntimeError("SECRET_ACCESS_TOKEN")])
def test_failures_do_not_reveal_upstream_or_stale_content(monkeypatch, failure):
    initial = [] if isinstance(failure, httpx.Response) and failure.status_code == 304 else [response()]
    source, clock, _ = scripted(monkeypatch, initial + [failure])
    if initial:
        assert read(source)["items"]
        clock.advance(60)
    result = read(source)
    assert result["items"] == []
    assert not result["refresh"]["stale"]
    assert result["refresh"]["status"] == "error"
    assert "SECRET_ACCESS_TOKEN" not in json.dumps(result)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def transport(monkeypatch, handler):
    real_client, options = httpx.AsyncClient, []

    def client(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(github.httpx, "AsyncClient", client)
    return options


def test_fetch_is_fixed_anonymous_and_bounded(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=Chunks([b"[]"]))

    monkeypatch.setenv("GITHUB_TOKEN", "DO_NOT_SEND")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    options = transport(monkeypatch, handler)
    assert asyncio.run(github._fetch("dify", '"synthetic"')).json() == []
    assert options == [{"timeout": 12.0, "follow_redirects": False, "trust_env": False}]
    assert str(requests[0].url) == "https://api.github.com/repos/langgenius/dify/releases?per_page=20&page=1"
    assert requests[0].headers["if-none-match"] == '"synthetic"'
    assert requests[0].headers["x-github-api-version"] == github.API_VERSION
    assert "authorization" not in requests[0].headers
    with pytest.raises(github.GithubFetchError):
        asyncio.run(github._fetch("https://internal.invalid", None))
    assert len(requests) == 1


@pytest.mark.parametrize("headers,chunks", [
    ({"Content-Type": "text/html"}, [b"secret upstream error"]),
    ({"Content-Type": "application/json", "Content-Encoding": "gzip"}, []),
    ({"Content-Type": "application/json", "Content-Length": str(github.MAX_RESPONSE_BYTES + 1)}, []),
    ({"Content-Type": "application/json"}, [b"x" * github.MAX_RESPONSE_BYTES, b"x"]),
])
def test_fetch_limits_body_and_rejects_unexpected_format(monkeypatch, headers, chunks):
    transport(monkeypatch, lambda request: httpx.Response(200, headers=headers, stream=Chunks(chunks)))
    with pytest.raises(github.GithubFetchError) as error:
        asyncio.run(github._fetch("langchain", None))
    assert "secret upstream error" not in str(error.value)


def test_redirect_body_is_never_read_or_followed(monkeypatch):
    requests = []

    class Unreadable(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("redirect body must not be read")
            yield b""

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}, stream=Unreadable())

    transport(monkeypatch, handler)
    assert asyncio.run(github._fetch("langchain", None)).status_code == 302
    assert len(requests) == 1


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("private endpoint"), RuntimeError("secret")])
def test_transport_errors_are_sanitized(monkeypatch, failure):
    def handler(request):
        raise failure

    transport(monkeypatch, handler)
    with pytest.raises(github.GithubFetchError) as error:
        asyncio.run(github._fetch("langchain", None))
    assert "private endpoint" not in str(error.value)
    assert "secret" not in str(error.value)


def test_concurrent_requests_share_fetch_and_do_not_block_event_loop(monkeypatch):
    calls, ticks = [], []

    async def fetch(project, etag):
        calls.append(project)
        await asyncio.sleep(0.02)
        return response()

    async def ticker():
        await asyncio.sleep(0)
        ticks.append(True)

    async def run():
        source = github.GithubSource(now=Clock())
        results = await asyncio.gather(source.list_items(github.GithubQuery()),
                                       source.list_items(github.GithubQuery(limit=1)), ticker())
        assert results[0]["items"] == results[1]["items"]

    monkeypatch.setattr(github, "_fetch", fetch)
    asyncio.run(run())
    assert calls == ["langchain"]
    assert ticks == [True]


def test_api_contract_validation_and_disabled_behavior(monkeypatch):
    from app.api.routes import github_sources as routes

    source, _, calls = scripted(monkeypatch, [response(rows=[release(1, "dify")])])
    monkeypatch.setattr(routes, "github_source", source)
    monkeypatch.setattr(routes.settings, "GITHUB_ENABLED", False, raising=False)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    with TestClient(app) as client:
        status = client.get("/api/source-hub/github/status")
        assert status.status_code == 200 and not status.json()["enabled"]
        disabled = client.get("/api/source-hub/github")
        assert disabled.json()["refresh"]["status"] == "disabled"
        assert disabled.headers["cache-control"] == "no-store"
        assert not calls
        for query in ("project=unknown", "limit=0", "limit=21"):
            assert client.get(f"/api/source-hub/github?{query}").status_code == 422
        assert not calls
        monkeypatch.setattr(routes.settings, "GITHUB_ENABLED", True)
        result = client.get("/api/source-hub/github?project=dify&limit=1")
        assert result.status_code == 200
        assert result.json()["query"] == {"project": "dify", "repository": "langgenius/dify", "limit": 1}
        assert result.json()["items"][0]["source_tier"] == "primary"

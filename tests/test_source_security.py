import asyncio
import socket

import httpx
import pytest

from app.services import safe_fetch as fetch
from app.services import source_importer


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks = chunks
        self.delay = delay

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk


def response(status=200, body=b"document", headers=None):
    return httpx.Response(status, stream=Chunks([body]), headers=headers or {})


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    configurations = []

    def client(**kwargs):
        configurations.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(fetch.httpx, "AsyncClient", client)
    return configurations


def set_dns(monkeypatch, addresses):
    async def lookup(host, port, **kwargs):
        ips = addresses(host) if callable(addresses) else addresses
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)


@pytest.mark.parametrize("url", [
    "http://localhost", "http://localhost.", "http://a.local", "http://metadata.google.internal",
    "http://127.0.0.1", "http://10.1.2.3", "http://172.16.0.1", "http://192.168.0.1",
    "http://169.254.169.254/latest/meta-data", "http://100.100.100.200", "http://0.0.0.0",
    "http://[::1]", "http://[fe80::1]", "http://[::ffff:127.0.0.1]",
    "http://[64:ff9b::a00:1]", "http://224.0.0.1",
    "file:///etc/passwd", "ftp://example.com", "gopher://example.com", "javascript:alert(1)",
    "https://user:secret@example.com", "https://example.com@localhost",
    "http://example.com:8080", "https://example.com:bad", "https://example.com\\@127.0.0.1",
    "https://example.com/\r\nsecret", "http://[fe80::1%25eth0]", "http://2130706433",
])
def test_rejects_nonpublic_or_ambiguous_urls(url):
    with pytest.raises(fetch.UnsafeSourceURL):
        source_importer.normalize_url(url)


def test_normalize_and_github_path_validation():
    assert source_importer.normalize_url(" example.com/a#fragment ") == "https://example.com/a"
    assert source_importer.parse_github_repo("https://github.com/example/project.git") == ("example", "project")
    for url in ["https://github.com/example/%2e%2e", "https://github.com/example/..", "https://github.com/a%2fb/repo"]:
        with pytest.raises(fetch.UnsafeSourceURL):
            source_importer.parse_github_repo(url)


@pytest.mark.parametrize("addresses", [["10.0.0.1"], ["93.184.216.34", "127.0.0.1"], ["::1"]])
def test_dns_private_or_mixed_answers_are_rejected(monkeypatch, addresses):
    async def run():
        set_dns(monkeypatch, addresses)
        with pytest.raises(fetch.UnsafeSourceURL):
            await fetch.safe_fetch("https://example.com")
    asyncio.run(run())


def test_connection_pins_validated_ip_and_retains_host_and_tls_name(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        return response(headers={"Content-Type": "text/plain"})
    configs = install_transport(monkeypatch, handler)
    async def run():
        set_dns(monkeypatch, ["93.184.216.34"])
        result = await fetch.safe_fetch("https://example.com/data?q=1", headers={"Authorization": "secret"})
        assert result.text == "document"
        assert str(result.url) == "https://example.com/data?q=1"
    asyncio.run(run())
    assert len(seen) == 1
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "example.com"
    assert seen[0].extensions["sni_hostname"] == "example.com"
    assert "authorization" not in seen[0].headers
    assert configs[0]["trust_env"] is False
    assert configs[0]["follow_redirects"] is False
    assert configs[0]["verify"] is True


@pytest.mark.parametrize("location", ["http://127.0.0.1/secret", "https://169.254.169.254/", "https://user:pass@example.com/", "file:///etc/passwd", "http://example.com/plain"])
def test_unsafe_redirect_is_rejected_without_following(monkeypatch, location):
    seen = []
    def handler(request):
        seen.append(request)
        return response(302, headers={"Location": location})
    install_transport(monkeypatch, handler)
    async def run():
        set_dns(monkeypatch, ["93.184.216.34"])
        with pytest.raises(fetch.UnsafeSourceURL):
            await fetch.safe_fetch("https://example.com/start")
    asyncio.run(run())
    assert len(seen) == 1


def test_same_host_redirect_resolves_again_and_rejects_rebinding(monkeypatch):
    requests = []
    dns_calls = []
    def handler(request):
        requests.append(request)
        return response(302, headers={"Location": "/next"})
    def dns(host):
        dns_calls.append(host)
        return ["93.184.216.34"] if len(dns_calls) == 1 else ["10.0.0.1"]
    install_transport(monkeypatch, handler)
    async def run():
        set_dns(monkeypatch, dns)
        with pytest.raises(fetch.UnsafeSourceURL):
            await fetch.safe_fetch("https://example.com/start")
    asyncio.run(run())
    assert len(requests) == 1
    assert dns_calls == ["example.com", "example.com"]


def test_redirect_limit_and_relative_redirect(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return response(302, headers={"Location": "/next"})
    install_transport(monkeypatch, handler)
    async def run():
        set_dns(monkeypatch, ["93.184.216.34"])
        with pytest.raises(fetch.SourceFetchError, match="跳转次数"):
            await fetch.safe_fetch("https://example.com/start", max_redirects=2)
    asyncio.run(run())
    assert len(requests) == 3
    assert str(requests[1].url) == "https://93.184.216.34/next"


@pytest.mark.parametrize("headers,chunks", [
    ({"Content-Length": "11"}, [b"small"]),
    ({}, [b"123456", b"123456"]),
    ({"Content-Encoding": "gzip"}, [b"compressed"]),
])
def test_response_limits_cover_declared_streamed_and_compressed_bodies(monkeypatch, headers, chunks):
    install_transport(monkeypatch, lambda request: httpx.Response(200, headers=headers, stream=Chunks(chunks)))
    async def run():
        set_dns(monkeypatch, ["93.184.216.34"])
        with pytest.raises(fetch.SourceFetchError):
            await fetch.safe_fetch("https://example.com", max_bytes=10)
    asyncio.run(run())


def test_deadline_covers_slow_body(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, stream=Chunks([b"a", b"b"], delay=0.1)))
    async def run():
        set_dns(monkeypatch, ["93.184.216.34"])
        with pytest.raises(fetch.SourceFetchError, match="超时"):
            await fetch.safe_fetch("https://example.com", timeout=0.01)
    asyncio.run(run())


def test_deadline_also_covers_dns(monkeypatch):
    async def run():
        async def slow_dns(*args, **kwargs):
            await asyncio.sleep(1)
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", slow_dns)
        with pytest.raises(fetch.SourceFetchError, match="超时"):
            await fetch.safe_fetch("https://example.com", timeout=0.01)
    asyncio.run(run())


def test_transport_errors_do_not_leak_urls_or_secrets(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("https://secret.internal?api_key=DO_NOT_LEAK")
    install_transport(monkeypatch, handler)
    async def run():
        set_dns(monkeypatch, ["93.184.216.34"])
        with pytest.raises(fetch.SourceFetchError) as error:
            await fetch.safe_fetch("https://example.com/?token=DO_NOT_LEAK")
        assert "DO_NOT_LEAK" not in str(error.value)
        assert "secret.internal" not in str(error.value)
    asyncio.run(run())


def test_security_rejection_cannot_be_hidden_by_fallback_summary(monkeypatch):
    async def run():
        set_dns(monkeypatch, ["10.0.0.1"])
        with pytest.raises(fetch.UnsafeSourceURL):
            await source_importer.import_source_from_url("https://example.com", fallback_summary="manual text")
    asyncio.run(run())


def test_github_fetch_uses_same_bounded_gateway_and_allows_absent_readme(monkeypatch):
    calls = []
    async def fake_fetch(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/readme"):
            return httpx.Response(404, text="missing")
        return httpx.Response(200, json={"full_name": "owner/repo", "description": "project"})
    monkeypatch.setattr(source_importer, "safe_fetch", fake_fetch)
    imported = asyncio.run(source_importer.import_github_repo("https://github.com/owner/repo"))
    assert imported.source_type == "github"
    assert len(calls) == 2
    assert all(kwargs["allowed_hosts"] == {"api.github.com"} for _, kwargs in calls)
    assert calls[1][1]["allowed_error_statuses"] == frozenset({404})


def test_github_redirect_cannot_escape_allowlist(monkeypatch):
    install_transport(monkeypatch, lambda request: response(302, headers={"Location": "https://example.com/"}))
    async def run():
        set_dns(monkeypatch, ["93.184.216.34"])
        with pytest.raises(fetch.UnsafeSourceURL):
            await fetch.safe_fetch("https://api.github.com/repos/a/b", allowed_hosts={"api.github.com"})
    asyncio.run(run())

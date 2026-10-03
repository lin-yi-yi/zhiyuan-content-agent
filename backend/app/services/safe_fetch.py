"""Bounded public-HTTP fetches for user-supplied sources.

Each hop resolves and validates every address, then connects to a selected numeric
IP. Host and TLS SNI retain the original hostname; environment proxies are disabled.
This closes the application-level DNS check/connect gap, but is not an egress
firewall: routing/NAT, a compromised resolver, and public servers proxying private
content remain outside this module's trust boundary. Keep network egress controls
for an Internet-facing deployment. The SNI extension is supported by httpcore 1.x.
"""
import asyncio
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
FETCH_TIMEOUT_SECONDS = 20.0
_REDIRECTS = {301, 302, 303, 307, 308}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".lan", ".home")
_NAT64_NETWORKS = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


class SourceFetchError(ValueError):
    """A public-safe failure; never include URLs, headers or upstream messages."""


class UnsafeSourceURL(SourceFetchError):
    pass


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            return False
        if any(address in network for network in _NAT64_NETWORKS):
            return False
    return True


def normalize_public_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip() or len(url) > 8192:
        raise UnsafeSourceURL("URL 格式不正确")
    if re.search(r"[\x00-\x1f\x7f\\]", url):
        raise UnsafeSourceURL("URL 含不允许的字符")
    value = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise ValueError
        host = parts.hostname.rstrip(".").lower().encode("idna").decode("ascii")
        port = parts.port
        if port not in {None, 80 if parts.scheme.lower() == "http" else 443}:
            raise ValueError
        if not host or "%" in host or any(character.isspace() for character in host):
            raise ValueError
    except (ValueError, UnicodeError):
        raise UnsafeSourceURL("仅支持不含凭证的标准 HTTP/HTTPS 公网地址") from None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith(_BLOCKED_SUFFIXES):
            raise UnsafeSourceURL("不允许访问本机或内部网络地址")
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
            raise UnsafeSourceURL("URL 主机名不正确")
    else:
        if not _public_ip(host):
            raise UnsafeSourceURL("不允许访问本机或内部网络地址")
    authority = f"[{host}]" if ":" in host else host
    return urlunsplit((parts.scheme.lower(), authority, parts.path or "/", parts.query, ""))


async def _resolve_public_addresses(host: str, port: int) -> list[str]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            results = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise SourceFetchError("无法解析信源地址") from None
        addresses = list(dict.fromkeys(row[4][0] for row in results))
    else:
        addresses = [str(literal)]
    if not addresses or not all(_public_ip(address) for address in addresses):
        raise UnsafeSourceURL("信源地址解析到不允许访问的网络")
    return addresses


async def safe_fetch(
    url: str, *, headers: dict[str, str] | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES, timeout: float = FETCH_TIMEOUT_SECONDS,
    max_redirects: int = MAX_REDIRECTS, allowed_hosts: set[str] | None = None,
    allowed_error_statuses: frozenset[int] = frozenset(),
) -> httpx.Response:
    """Fetch a small uncompressed document, validating and pinning every hop.

    Limits include DNS, redirects and the entire streamed body. Only Accept is
    forwarded from callers; credentials/cookies cannot cross redirect boundaries.
    """
    try:
        async with asyncio.timeout(timeout):
            current = normalize_public_url(url)
            for hop in range(max_redirects + 1):
                parts = urlsplit(current)
                host = parts.hostname
                if allowed_hosts is not None and host not in allowed_hosts:
                    raise UnsafeSourceURL("信源跳转到不允许的主机")
                port = 443 if parts.scheme == "https" else 80
                addresses = await _resolve_public_addresses(host, port)
                # The request URL is numeric: the transport cannot re-resolve the
                # original name to a different address after our validation.
                selected_ip = addresses[0]
                ip_authority = f"[{selected_ip}]" if ":" in selected_ip else selected_ip
                connect_url = urlunsplit((parts.scheme, ip_authority, parts.path, parts.query, ""))
                request_headers = {
                    "User-Agent": "ai-content-agent/0.6",
                    "Host": parts.netloc,
                    "Accept-Encoding": "identity",
                    "Accept": (headers or {}).get("Accept", "text/html,text/plain,application/json"),
                }
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout), follow_redirects=False,
                    trust_env=False, verify=True,
                ) as client:
                    async with client.stream(
                        "GET", connect_url, headers=request_headers,
                        extensions={"sni_hostname": host},
                    ) as response:
                        if response.status_code in _REDIRECTS:
                            if hop >= max_redirects:
                                raise SourceFetchError("信源跳转次数超过限制")
                            location = response.headers.get("location")
                            if not location:
                                raise SourceFetchError("信源跳转缺少目标地址")
                            target = normalize_public_url(urljoin(current, location))
                            if parts.scheme == "https" and urlsplit(target).scheme != "https":
                                raise UnsafeSourceURL("不允许降低信源连接的安全等级")
                            current = target
                            continue
                        if response.status_code >= 400 and response.status_code not in allowed_error_statuses:
                            raise SourceFetchError("信源服务器未能返回可用内容")
                        if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                            raise SourceFetchError("暂不接受压缩信源，请提供正文或其他来源")
                        length = response.headers.get("content-length")
                        if length is not None:
                            try:
                                size = int(length)
                            except ValueError:
                                raise SourceFetchError("信源响应长度不正确") from None
                            if size < 0 or size > max_bytes:
                                raise SourceFetchError("信源正文超过大小限制")
                        body = bytearray()
                        async for chunk in response.aiter_raw():
                            if len(body) + len(chunk) > max_bytes:
                                raise SourceFetchError("信源正文超过大小限制")
                            body.extend(chunk)
                        return httpx.Response(
                            response.status_code, headers=response.headers,
                            content=bytes(body), request=httpx.Request("GET", current),
                        )
    except SourceFetchError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise SourceFetchError("信源读取超时") from None
    except Exception:
        raise SourceFetchError("无法安全读取信源，请稍后重试") from None
    raise SourceFetchError("信源跳转次数超过限制")

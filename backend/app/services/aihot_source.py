"""Bounded, read-only AIHOT public feed. No database writes or MCP proxy.

Only the user's current public search terms leave this process. News is cached
temporarily in memory; attribution is retained and summaries remain unverified.
"""
import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import math
import time
from typing import Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from app.services.safe_fetch import normalize_public_url, UnsafeSourceURL


ITEMS_URL = "https://aihot.news/api/v1/items"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 12.0
CACHE_ENTRIES = 16
MIN_REFRESH_SECONDS = 60
STALE_GRACE_SECONDS = 300
CATEGORIES = {"ai-models", "ai-products", "industry", "paper", "tip"}


class AihotQuery(BaseModel):
    mode: Literal["selected", "all"] = "selected"
    window: Literal["24h", "7d"] = "24h"
    q: str = ""
    category: str = ""
    limit: int = Field(default=20, ge=1, le=30)

    @field_validator("q", "category", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("q")
    @classmethod
    def validate_query(cls, value):
        if value and not 2 <= len(value) <= 200:
            raise ValueError("关键词去除首尾空白后需为 2 至 200 个字符")
        return value

    @field_validator("category")
    @classmethod
    def validate_category(cls, value):
        if value and value not in CATEGORIES:
            raise ValueError("不支持的来源分类")
        return value

    def public_query(self):
        return {"mode": self.mode, "window": self.window, "q": self.q,
                "category": self.category, "by": "timeline"}

    def params(self):
        return {key: value for key, value in {**self.public_query(), "limit": self.limit}.items() if value != ""}


def source_status(enabled: bool) -> dict:
    return {"name": "AIHOT", "homepage_url": "https://aihot.news/agent",
            "mcp_url": "https://aihot.news/api/mcp", "terms_url": "https://aihot.news/terms",
            "enabled": bool(enabled), "embed_supported": False, "read_only": True}


class AihotFetchError(ValueError):
    """Messages are fixed strings; upstream bodies and query URLs are never exposed."""

    def __init__(self, message, *, stale_allowed=False):
        super().__init__(message)
        self.stale_allowed = stale_allowed


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None


def _ttl(headers, fallback=MIN_REFRESH_SECONDS):
    match = re.search(r"(?:^|,)\s*s-maxage\s*=\s*\"?(\d{1,9})\"?\s*(?:,|$)",
                      headers.get("cache-control", ""), flags=re.I)
    return max(MIN_REFRESH_SECONDS, int(match.group(1))) if match else fallback


def _retry_seconds(headers, now):
    value = headers.get("retry-after", "").strip()
    if re.fullmatch(r"\d{1,9}", value):
        return max(MIN_REFRESH_SECONDS, int(value))
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(MIN_REFRESH_SECONDS, date.timestamp() - now)
    except (TypeError, ValueError, OverflowError):
        return MIN_REFRESH_SECONDS


def _safe_link(value):
    if not isinstance(value, str) or not re.match(r"^https?://", value, re.I):
        return None
    try:
        normalize_public_url(value)
        return value  # Preserve the upstream canonical spelling after validation.
    except UnsafeSourceURL:
        return None


def _safe_values(value, *, key="", links=False, depth=0):
    """Keep future fields, including rights, but never return unsafe link targets."""
    if depth > 12:
        raise AihotFetchError("AIHOT 返回的数据结构不受支持")
    if isinstance(value, dict):
        return {name: _safe_values(item, key=name, links=links or key.lower() == "links", depth=depth + 1)
                for name, item in value.items()}
    if isinstance(value, list):
        return [_safe_values(item, key=key, links=links, depth=depth + 1) for item in value]
    if isinstance(value, str):
        lower = key.lower()
        if links or lower in {"url", "uri", "href", "link", "canonical"} or lower.endswith(("url", "uri")):
            return _safe_link(value)
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise AihotFetchError("AIHOT 返回的数据格式不受支持")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise AihotFetchError("AIHOT 返回的数据格式不受支持")


def _payload(response, query):
    try:
        body = response.json()
        if not isinstance(body, dict) or body.get("schemaVersion", 1) != 1:
            raise ValueError
        items, page = body["items"], body["page"]
        if not isinstance(items, list) or len(items) > query.limit or not isinstance(page, dict):
            raise ValueError
        if (type(page.get("count")) is not int or page["count"] < 0
                or type(page.get("hasMore")) is not bool
                or "nextCursor" not in page or not isinstance(page["nextCursor"], (str, type(None)))):
            raise ValueError
        echoed = body.get("query", {})
        if not isinstance(echoed, dict):
            raise ValueError
        for key, expected in query.public_query().items():
            if key in echoed and (echoed[key] or "") != expected:
                raise ValueError
        output = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ValueError
            if not isinstance(item.get("title"), str) or not isinstance(item.get("source"), dict):
                raise ValueError
            if not isinstance(item["source"].get("name"), str):
                raise ValueError
            if not isinstance(item.get("links"), dict):
                raise ValueError
            for key in ("summary", "reason", "originalTitle", "publishedAt", "discoveredAt", "category"):
                if key in item and not isinstance(item[key], (str, type(None))):
                    raise ValueError
            score = item.get("score")
            if score is not None and (type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 100):
                raise ValueError
            if "selected" in item and type(item["selected"]) is not bool:
                raise ValueError
            for key in ("aihot", "original"):
                if key in item["links"] and not isinstance(item["links"][key], (str, type(None))):
                    raise ValueError
            if "attribution" in item:
                attribution = item["attribution"]
                if (not isinstance(attribution, dict) or not isinstance(attribution.get("name"), str)
                        or not isinstance(attribution.get("url"), (str, type(None)))):
                    raise ValueError
            safe = _safe_values(item)
            safe.update(verification_status="unverified", content_kind="ai_summary")
            output.append(safe)
        return {"items": output, "page": _safe_values(page)}
    except (ValueError, TypeError, KeyError, RecursionError):
        raise AihotFetchError("AIHOT 返回的数据格式不受支持，请稍后重试") from None


async def _fetch(params: dict, etag: str | None) -> httpx.Response:
    headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "zhiyuan-personal-reader/0.6"}
    if etag and len(etag) <= 1024 and not re.search(r"[\x00-\x1f\x7f]", etag):
        headers["If-None-Match"] = etag
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", ITEMS_URL, params=params, headers=headers) as response:
                    # Redirects and failures need no body; never follow their location.
                    if response.status_code != 200:
                        return httpx.Response(response.status_code, headers=response.headers)
                    if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                        raise AihotFetchError("AIHOT 返回了不支持的压缩格式")
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        raise AihotFetchError("AIHOT 未返回可用的数据格式")
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                        raise AihotFetchError("AIHOT 响应超过大小限制")
                    data = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise AihotFetchError("AIHOT 响应超过大小限制")
                        data.extend(chunk)
                    return httpx.Response(200, headers=response.headers, content=bytes(data))
    except AihotFetchError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise AihotFetchError("AIHOT 读取超时，请稍后重试", stale_allowed=True) from None
    except Exception:
        raise AihotFetchError("暂时无法读取 AIHOT，请稍后重试", stale_allowed=True) from None


@dataclass
class _Entry:
    payload: dict | None = None
    etag: str | None = None
    ttl: float = MIN_REFRESH_SECONDS
    last_attempt: float | None = None
    last_success: float | None = None
    next_refresh: float = 0
    stale_until: float = 0
    allow_stale: bool = True
    cache_directives: frozenset = frozenset()
    error: str | None = None


class AihotSource:
    def __init__(self, now=time.time):
        self.now = now
        self._cache: OrderedDict[tuple, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._backoff_until = 0
        self._backoff_error = None

    async def list_items(self, query: AihotQuery, enabled: bool = True) -> dict:
        if not enabled:
            return self._result(query, _Entry(), enabled=False, status="disabled", from_cache=False)
        # This is a small personal reader, not a distributed cache or polling worker.
        async with self._lock:
            now = self.now()
            key = (query.mode, query.window, query.q, query.category, query.limit)
            entry = self._cache.get(key)
            if entry is None:
                entry = _Entry()
                self._cache[key] = entry
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
            if now < entry.next_refresh:
                return self._result(query, entry, from_cache=True)
            if now < self._backoff_until:
                entry.error, entry.next_refresh = self._backoff_error, self._backoff_until
                return self._result(query, entry, from_cache=True)
            entry.last_attempt = now
            try:
                response = await _fetch(query.params(), entry.etag)
                now = self.now()
                if response.status_code == 403:
                    # Access revocation applies to the provider, not just this query.
                    self._cache.clear()
                    self._cache[key] = entry
                    self._backoff_until = now + MIN_REFRESH_SECONDS
                    self._backoff_error = "AIHOT 暂时拒绝访问，请等待下次可刷新时间"
                    entry.next_refresh = self._backoff_until
                    raise AihotFetchError(self._backoff_error)
                if response.status_code in {429, 503}:
                    self._backoff_until = now + _retry_seconds(response.headers, now)
                    self._backoff_error = "AIHOT 暂时限流或维护，请等待下次可刷新时间"
                    entry.next_refresh = self._backoff_until
                    raise AihotFetchError(self._backoff_error, stale_allowed=True)
                if response.status_code == 304:
                    if entry.payload is None:
                        raise AihotFetchError("AIHOT 缓存校验失败，请稍后重试")
                elif response.status_code == 200:
                    entry.payload = _payload(response, query)
                    entry.etag = response.headers.get("etag")
                else:
                    raise AihotFetchError("AIHOT 暂未返回可用内容，请稍后重试")
                entry.ttl = _ttl(response.headers, entry.ttl if response.status_code == 304 else MIN_REFRESH_SECONDS)
                entry.etag = response.headers.get("etag", entry.etag)
                entry.last_success, entry.error = now, None
                entry.next_refresh = now + entry.ttl
                entry.stale_until = entry.next_refresh + STALE_GRACE_SECONDS
                directives = frozenset(part.strip().split("=", 1)[0].lower()
                                      for part in response.headers.get("cache-control", "").split(","))
                if response.status_code == 304 and "cache-control" not in response.headers:
                    directives = entry.cache_directives
                entry.cache_directives = directives
                entry.allow_stale = "no-cache" not in directives and "must-revalidate" not in directives
                result = self._result(query, entry, from_cache=response.status_code == 304)
                if "no-store" in directives:
                    # Retain only cooldown metadata; no body or validators survive.
                    entry.payload, entry.etag = None, None
                    entry.error = "AIHOT 要求不缓存正文，请等待下次可刷新时间"
                elif "no-cache" in directives:
                    # Keep the validator for the next permitted request, but do not
                    # display unvalidated content during the minimum interval.
                    entry.error = "AIHOT 要求重新验证内容，请等待下次可刷新时间"
                return result
            except Exception as exc:
                entry.error = str(exc) if isinstance(exc, AihotFetchError) else "暂时无法读取 AIHOT，请稍后重试"
                can_keep = (isinstance(exc, AihotFetchError) and exc.stale_allowed) or isinstance(exc, (httpx.RequestError, TimeoutError))
                if not can_keep:
                    # Revoked/deleted resources and invalid responses must not be revived.
                    entry.payload, entry.etag, entry.stale_until = None, None, 0
                entry.next_refresh = max(entry.next_refresh, self.now() + MIN_REFRESH_SECONDS)
                return self._result(query, entry, from_cache=entry.payload is not None)

    def _result(self, query, entry, *, enabled=True, status=None, from_cache=False):
        status = status or ("error" if entry.error else "ready")
        usable = enabled and entry.payload is not None and (
            not entry.error or (entry.allow_stale and self.now() <= entry.stale_until))
        payload = deepcopy(entry.payload) if usable else {"items": [], "page": {"count": 0, "hasMore": False, "nextCursor": None}}
        return {"provider": "aihot", "source": source_status(enabled), "query": query.public_query(),
                **payload, "refresh": {"status": status, "last_attempt_at": _iso(entry.last_attempt),
                    "last_success_at": _iso(entry.last_success),
                    "next_refresh_at": _iso(entry.next_refresh) if entry.next_refresh else None,
                    "stale": bool(usable and entry.error), "from_cache": from_cache, "error": entry.error}}


aihot_source = AihotSource()

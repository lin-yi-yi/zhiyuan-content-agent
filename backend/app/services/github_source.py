"""Read-only release metadata from three explicitly allowlisted maintainers.

No token, arbitrary URL, release body, asset download, or database write is used.
GitHub's public API access does not grant blanket rights to redistribute content;
only a short metadata description and the original release link are returned.
API contract: https://docs.github.com/en/rest/releases/releases#list-releases
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field


PROJECTS = {
    "langchain": "langchain-ai/langchain",
    "dify": "langgenius/dify",
    "ragflow": "infiniflow/ragflow",
}
API_ROOT = "https://api.github.com/repos"
API_VERSION = "2026-03-10"
FETCH_TIMEOUT_SECONDS = 12.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
CACHE_SECONDS = 60
FETCH_LIMIT = 20


class GithubQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: Literal["langchain", "dify", "ragflow"] = "langchain"
    limit: int = Field(default=5, ge=1, le=20)

    @property
    def repository(self):
        return PROJECTS[self.project]


class GithubFetchError(ValueError):
    """Fixed public-safe error; never forward upstream error text or bodies."""


def source_status(enabled: bool):
    return {
        "name": "GitHub Releases", "enabled": bool(enabled), "read_only": True,
        "homepage_url": "https://github.com", "embed_supported": False,
        "docs_url": "https://docs.github.com/en/rest/releases/releases#list-releases",
        "terms_url": "https://docs.github.com/en/site-policy/github-terms/github-terms-of-service",
        "projects": [{"key": key, "repository": repo, "url": f"https://github.com/{repo}/releases"}
                     for key, repo in PROJECTS.items()],
        "discovered_at_basis": "connector_fetch_time",
        "content_policy": "仅显示发布元数据；不展示或保存发布正文，不下载附件；原仓库许可证不等于其他新闻内容的使用授权。",
    }


def _iso(timestamp):
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _published(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return value


def _release_link(value, repository):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\x00-\x20\x7f\\]", value):
        raise ValueError
    url = urlsplit(value)
    if (url.scheme != "https" or url.netloc != "github.com" or url.query or url.fragment
            or not url.path.startswith(f"/{repository}/releases/")):
        raise ValueError
    return value


def _metadata(response, query, fetched_at):
    try:
        rows = response.json()
        if not isinstance(rows, list) or len(rows) > FETCH_LIMIT:
            raise ValueError
        items = []
        identifiers = set()
        for release in rows:
            if not isinstance(release, dict) or type(release.get("draft")) is not bool:
                raise ValueError
            if release["draft"]:
                continue
            identifier, tag = release.get("id"), release.get("tag_name")
            if (type(identifier) is not int or identifier < 1 or identifier in identifiers
                    or not isinstance(tag, str) or not tag.strip() or len(tag) > 512
                    or type(release.get("prerelease")) is not bool):
                raise ValueError
            identifiers.add(identifier)
            name = release.get("name")
            if name is not None and (not isinstance(name, str) or len(name) > 1024):
                raise ValueError
            link = _release_link(release.get("html_url"), query.repository)
            label = " ".join(tag.split())
            title = " ".join((name or tag).split())
            kind = "预发布" if release["prerelease"] else "正式发布"
            items.append({
                "id": f"github:{query.repository}:{identifier}", "title": title,
                "summary": f"{query.repository} 发布 {label}（{kind}）。变更细节请查看维护者的原始发布页。",
                "source": {"name": query.repository}, "links": {"original": link, "github": link},
                "publishedAt": _published(release.get("published_at")), "discoveredAt": _iso(fetched_at),
                "discovered_at_basis": "connector_fetch_time", "version_label": label,
                "prerelease": release["prerelease"], "verification_status": "unverified",
                "content_kind": "maintainer_release", "source_tier": "primary",
            })
        return items, bool(re.search(r';\s*rel="next"', response.headers.get("link", "")))
    except (ValueError, TypeError, KeyError, RecursionError):
        raise GithubFetchError("GitHub 返回的发布数据格式不受支持，请稍后重试。") from None


async def _fetch(project: str, etag: str | None):
    if project not in PROJECTS:
        raise GithubFetchError("仅支持列表中的 GitHub 项目。")
    url = f"{API_ROOT}/{PROJECTS[project]}/releases"
    headers = {"Accept": "application/vnd.github+json", "Accept-Encoding": "identity",
               "X-GitHub-Api-Version": API_VERSION, "User-Agent": "zhiyuan-personal-reader/0.6"}
    if etag and len(etag) <= 1024 and not re.search(r"[\x00-\x1f\x7f]", etag):
        headers["If-None-Match"] = etag
    try:
        async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", url, params={"per_page": FETCH_LIMIT, "page": 1}, headers=headers) as response:
                    if response.status_code != 200:
                        return httpx.Response(response.status_code, headers=response.headers)
                    if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                        raise GithubFetchError("GitHub 返回了不支持的压缩格式。")
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        raise GithubFetchError("GitHub 未返回可用的数据格式。")
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                        raise GithubFetchError("GitHub 响应超过大小限制。")
                    data = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise GithubFetchError("GitHub 响应超过大小限制。")
                        data.extend(chunk)
                    return httpx.Response(200, headers=response.headers, content=bytes(data))
    except GithubFetchError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise GithubFetchError("GitHub 读取超时，请稍后重试。") from None
    except Exception:
        raise GithubFetchError("暂时无法读取 GitHub，请稍后重试。") from None


def _backoff_seconds(headers, now, failures):
    delay = min(3600, CACHE_SECONDS * 2 ** min(failures - 1, 6))
    retry_after = headers.get("retry-after", "").strip()
    if re.fullmatch(r"\d{1,9}", retry_after):
        delay = max(delay, int(retry_after))
    elif retry_after:
        try:
            value = parsedate_to_datetime(retry_after)
            value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            delay = max(delay, value.timestamp() - now)
        except (TypeError, ValueError, OverflowError):
            pass
    reset = headers.get("x-ratelimit-reset", "")
    if headers.get("x-ratelimit-remaining") == "0" and re.fullmatch(r"\d{1,12}", reset):
        delay = max(delay, int(reset) - now)
    return max(CACHE_SECONDS, delay)


@dataclass
class _Entry:
    items: list | None = None
    has_more: bool = False
    etag: str | None = None
    last_attempt: float | None = None
    last_success: float | None = None
    next_refresh: float = 0
    error: str | None = None
    directives: frozenset = frozenset()


class GithubSource:
    def __init__(self, now=time.time):
        self.now = now
        self._cache: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()
        self._backoff_until = 0
        self._backoff_error = None
        self._failures = 0

    async def list_items(self, query: GithubQuery, enabled=True):
        if not enabled:
            return self._result(query, _Entry(), enabled=False, status="disabled")
        async with self._lock:
            now = self.now()
            if now < self._backoff_until:
                return self._result(query, _Entry(next_refresh=self._backoff_until, error=self._backoff_error))
            entry = self._cache.setdefault(query.project, _Entry())
            if now < entry.next_refresh:
                return self._result(query, entry, from_cache=True)
            entry.last_attempt = now
            try:
                response = await _fetch(query.project, entry.etag)
                now = self.now()
                if response.status_code in {403, 429, 503}:
                    self._failures += 1
                    self._backoff_until = now + _backoff_seconds(response.headers, now, self._failures)
                    self._backoff_error = "GitHub 暂时拒绝访问、限流或维护，请等待下次可刷新时间。"
                    self._cache.clear()
                    entry.next_refresh = self._backoff_until
                    raise GithubFetchError(self._backoff_error)
                if response.status_code == 200:
                    entry.items, entry.has_more = _metadata(response, query, now)
                    entry.etag = response.headers.get("etag")
                elif response.status_code != 304 or entry.items is None:
                    raise GithubFetchError("GitHub 暂未返回可用的公开发布信息，请稍后重试。")
                if response.status_code != 304 or "cache-control" in response.headers:
                    entry.directives = frozenset(part.strip().split("=", 1)[0].lower()
                                                 for part in response.headers.get("cache-control", "").split(","))
                entry.etag = response.headers.get("etag", entry.etag)
                entry.last_success, entry.error = now, None
                entry.next_refresh = now + CACHE_SECONDS
                self._failures = 0
                result = self._result(query, entry, from_cache=response.status_code == 304)
                if "no-store" in entry.directives:
                    entry.items, entry.etag = None, None
                    entry.error = "GitHub 要求不缓存内容，请等待下次可刷新时间。"
                elif "no-cache" in entry.directives:
                    entry.error = "GitHub 要求重新验证内容，请等待下次可刷新时间。"
                return result
            except Exception as exc:
                entry.items, entry.etag = None, None
                entry.error = str(exc) if type(exc) is GithubFetchError else "暂时无法读取 GitHub，请稍后重试。"
                entry.next_refresh = max(entry.next_refresh, self.now() + CACHE_SECONDS)
                return self._result(query, entry)

    def _result(self, query, entry, *, enabled=True, status=None, from_cache=False):
        items = deepcopy(entry.items[:query.limit]) if enabled and entry.items is not None and not entry.error else []
        return {
            "provider": "github", "source": source_status(enabled),
            "query": {"project": query.project, "repository": query.repository, "limit": query.limit},
            "items": items, "page": {"count": len(items), "hasMore": bool(items and (entry.has_more or len(entry.items) > query.limit)), "nextCursor": None},
            "refresh": {"status": status or ("error" if entry.error else "ready"),
                        "last_attempt_at": _iso(entry.last_attempt), "last_success_at": _iso(entry.last_success),
                        "next_refresh_at": _iso(entry.next_refresh) if entry.next_refresh else None,
                        "stale": False, "from_cache": from_cache, "error": entry.error},
        }


github_source = GithubSource()

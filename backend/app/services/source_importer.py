"""URL / GitHub 信源导入服务。"""
from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import urlparse

from app.services.safe_fetch import SourceFetchError, UnsafeSourceURL, normalize_public_url, safe_fetch


@dataclass
class ImportedSource:
    source_type: str
    title: str
    url: str
    raw_content: str
    summary: str
    topic_title: str


@dataclass
class TopicSuggestion:
    title: str
    content_angle: str
    target_audience: str
    summary: str
    reason: str
    risk_tip: str


class ReadableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.description = ""
        self._in_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        attrs_dict = {k.lower(): v for k, v in attrs if k}
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            name = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            if name in {"description", "og:description", "twitter:description"} and attrs_dict.get("content"):
                self.description = attrs_dict["content"].strip()

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str):
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title += text
        elif self._skip_depth == 0:
            self._chunks.append(text)

    @property
    def readable_text(self) -> str:
        return clean_text("\n".join(self._chunks))


async def import_source_from_url(url: str, source_type: str = "", fallback_summary: str = "") -> ImportedSource:
    normalized_url = normalize_url(url)
    if is_github_repo_url(normalized_url):
        try:
            return await import_github_repo(normalized_url)
        except UnsafeSourceURL:
            raise
        except Exception:
            if not fallback_summary:
                raise
            return ImportedSource(
                source_type="github",
                title=repo_title_from_url(normalized_url),
                url=normalized_url,
                raw_content=fallback_summary,
                summary=make_summary(fallback_summary),
                topic_title=f"开源项目拆解：{repo_title_from_url(normalized_url)}",
            )

    try:
        return await import_webpage(normalized_url, source_type=source_type or "other")
    except UnsafeSourceURL:
        raise
    except Exception:
        if not fallback_summary:
            raise
        title = title_from_url(normalized_url)
        return ImportedSource(
            source_type=source_type or "other",
            title=title,
            url=normalized_url,
            raw_content=fallback_summary,
            summary=make_summary(fallback_summary),
            topic_title=title,
        )


async def import_github_repo(url: str) -> ImportedSource:
    owner, repo = parse_github_repo(url)
    headers = {"Accept": "application/vnd.github+json"}
    repo_resp = await safe_fetch(
        f"https://api.github.com/repos/{owner}/{repo}", headers=headers, allowed_hosts={"api.github.com"},
    )
    try:
        repo_data = repo_resp.json()
        if not isinstance(repo_data, dict):
            raise ValueError
    except (ValueError, TypeError):
        raise SourceFetchError("GitHub 未返回有效仓库信息") from None

    readme_resp = await safe_fetch(
        f"https://api.github.com/repos/{owner}/{repo}/readme",
        headers={"Accept": "application/vnd.github.raw"}, allowed_hosts={"api.github.com"},
        allowed_error_statuses=frozenset({404}),
    )
    readme_text = readme_resp.text if readme_resp.status_code < 400 else ""

    full_name = repo_data.get("full_name") or f"{owner}/{repo}"
    description = repo_data.get("description") or ""
    stars = repo_data.get("stargazers_count") or 0
    language = repo_data.get("language") or ""
    title = f"{full_name} - {description}" if description else full_name
    raw_content = clean_text(f"{description}\n\nStars: {stars}\nLanguage: {language}\n\n{readme_text}")
    summary = make_summary(raw_content)

    return ImportedSource(
        source_type="github",
        title=trim(title, 255),
        url=url,
        raw_content=raw_content,
        summary=summary,
        topic_title=trim(f"开源项目拆解：{full_name}", 255),
    )


async def import_webpage(url: str, source_type: str) -> ImportedSource:
    resp = await safe_fetch(url)
    content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"text/html", "text/plain"}:
        raise SourceFetchError("暂不支持该信源的内容类型")
    html = resp.text

    parser = ReadableHTMLParser()
    parser.feed(html)
    title = trim(clean_text(parser.title) or title_from_url(url), 255)
    raw_content = parser.readable_text
    if len(raw_content) < 80:
        raise ValueError("页面正文过短，无法生成有效选题")
    summary_base = f"{parser.description}\n\n{raw_content}" if parser.description else raw_content
    summary = make_summary(summary_base)

    return ImportedSource(
        source_type=source_type,
        title=title,
        url=url,
        raw_content=raw_content,
        summary=summary,
        topic_title=title,
    )


def normalize_url(url: str) -> str:
    return normalize_public_url(url)


def is_github_repo_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower() in {"github.com", "www.github.com"} and len(path_parts(parsed.path)) >= 2


def parse_github_repo(url: str) -> tuple[str, str]:
    parsed = urlparse(normalize_url(url))
    parts = path_parts(parsed.path)
    if parsed.hostname not in {"github.com", "www.github.com"} or len(parts) < 2:
        raise ValueError("GitHub 仓库链接需要包含 owner/repo")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo) or repo in {".", ".."}:
        raise UnsafeSourceURL("GitHub 仓库路径格式不正确")
    return owner, repo


def repo_title_from_url(url: str) -> str:
    try:
        owner, repo = parse_github_repo(url)
        return f"{owner}/{repo}"
    except Exception:
        return title_from_url(url)


def title_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = path_parts(parsed.path)
    return trim(parts[-1].replace("-", " ").replace("_", " ") if parts else parsed.netloc, 255)


def path_parts(path: str) -> list[str]:
    return [part for part in path.strip("/").split("/") if part]


def make_summary(text: str, limit: int = 900) -> str:
    cleaned = clean_text(text)
    return trim(cleaned, limit)


def clean_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip())


def trim(text: str, limit: int) -> str:
    value = " ".join(text.split())
    return value[: limit - 3] + "..." if len(value) > limit else value


def generate_topic_suggestions(imported: ImportedSource) -> list[TopicSuggestion]:
    base_title = imported.topic_title or imported.title
    summary = imported.summary or imported.raw_content
    keyword_title = trim(base_title.replace("开源项目拆解：", ""), 48)

    if imported.source_type == "github":
        suggestions = [
            TopicSuggestion(
                title=trim(f"开源项目拆解：{keyword_title}", 255),
                content_angle="案例拆解",
                target_audience="开发者 / AI 新手",
                summary=trim(f"拆解这个开源项目解决什么问题、核心工作流是什么、普通人能借鉴哪几步。{summary}", 900),
                reason="适合账号的 Agent 开发成长主线，也方便做成 7 页结构化卡片。",
                risk_tip="需要核对项目用途、README 描述和功能边界，避免夸大能力。",
            ),
            TopicSuggestion(
                title=trim(f"普通人能不能用 {keyword_title} 提效？", 255),
                content_angle="测评",
                target_audience="职场人 / AI 新手",
                summary=trim(f"从普通人的视角评估这个项目是否值得学习、安装成本如何、能复用到哪些工作任务。{summary}", 900),
                reason="测评视角更贴近小红书用户的实际决策，容易引发收藏和评论。",
                risk_tip="不要承诺所有人都能直接复用，要明确门槛和适用场景。",
            ),
            TopicSuggestion(
                title=trim(f"我从 {keyword_title} 学到的 Agent 工作流", 255),
                content_angle="开发日志",
                target_audience="AI 新手 / 开发者",
                summary=trim(f"把项目拆成输入、处理、工具调用、人工审核和复盘几个环节，记录自己的学习路径。{summary}", 900),
                reason="开发日志能强化账号人格，适合持续记录 AI 内容增长 Agent 的成长线。",
                risk_tip="需要区分自己的理解和项目官方能力，避免把推测写成事实。",
            ),
            TopicSuggestion(
                title=trim(f"{keyword_title} 值得新手学吗？先看这几个坑", 255),
                content_angle="避坑",
                target_audience="AI 新手 / 职场人",
                summary=trim(f"聚焦安装、配置、使用成本和误区，帮助新手判断是否适合投入时间。{summary}", 900),
                reason="避坑内容通常更容易触发收藏，也能降低用户试错成本。",
                risk_tip="避坑建议要基于真实体验或 README 信息，不要制造焦虑。",
            ),
        ]
    else:
        suggestions = [
            TopicSuggestion(
                title=trim(f"把这个方法做成一套 AI 工作流：{keyword_title}", 255),
                content_angle="教程",
                target_audience="职场人 / AI 新手",
                summary=trim(f"把信源内容转成普通人可复用的步骤：输入什么、让 AI 做什么、人工检查什么。{summary}", 900),
                reason="教程型内容最适合转成小红书图文卡片，收藏价值比较明确。",
                risk_tip="需要核对原文事实，不要把单个案例包装成通用结论。",
            ),
            TopicSuggestion(
                title=trim(f"普通人用 AI 提效，别忽略这个点：{keyword_title}", 255),
                content_angle="观点",
                target_audience="职场人 / 自媒体人",
                summary=trim(f"提炼信源中的一个关键观点，并结合普通人的工作场景解释为什么重要。{summary}", 900),
                reason="观点型内容适合建立账号判断力，不只是搬运资料。",
                risk_tip="观点要保留边界，避免绝对化表达。",
            ),
            TopicSuggestion(
                title=trim(f"我会怎么把它用到内容增长 Agent 里？", 255),
                content_angle="开发日志",
                target_audience="AI 新手 / 创作者",
                summary=trim(f"围绕自己的 AI 内容增长 Agent，说明这个信源能启发哪个模块、下一步怎么实验。{summary}", 900),
                reason="能把外部资料接回你的账号主线，形成连续成长记录。",
                risk_tip="要标清哪些已经实现、哪些只是计划，避免误导。",
            ),
            TopicSuggestion(
                title=trim(f"这类 AI 提效方法，最容易踩的 3 个坑", 255),
                content_angle="避坑",
                target_audience="AI 新手 / 职场人",
                summary=trim(f"从信源内容里提炼新手可能误解的地方，并转成可执行的避坑提醒。{summary}", 900),
                reason="避坑类内容更容易被收藏，适合做成清单式卡片。",
                risk_tip="不要为了制造冲突而夸大风险。",
            ),
        ]

    return suggestions[:5]

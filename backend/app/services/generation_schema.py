"""LLM 结构化输出规范化。

真实模型偶尔会少字段、字段类型飘、卡片数量不足。这里统一在入库前补齐，
避免前端和数据库直接承受模型输出的不稳定性。
"""
from typing import Any


CARD_TYPES = ["cover", "pain_point", "concept", "workflow", "case", "pitfall", "summary"]


def normalize_topic_score_result(result: dict[str, Any]) -> dict[str, Any]:
    score = _int(result.get("score"), 0)
    score = max(0, min(100, score))
    risk_level = _choice(result.get("risk_level"), ["low", "medium", "high"], "low")
    recommended_platform = _choice(
        result.get("recommended_platform"),
        ["小红书图文", "抖音图文", "暂不推荐"],
        "小红书图文" if score >= 60 and risk_level != "high" else "暂不推荐",
    )
    return {
        "concise_summary": _text(result.get("concise_summary"), "暂无摘要")[:180],
        "target_audience": _text(result.get("target_audience"), "职场人 / AI 新手")[:100],
        "content_angle": _text(result.get("content_angle"), "教程")[:100],
        "recommended_platform": recommended_platform,
        "score": score,
        "score_reason": _text(result.get("score_reason"), "模型未给出详细评分理由。"),
        "risk_level": risk_level,
    }


def normalize_draft_result(result: dict[str, Any], topic: Any) -> dict[str, Any]:
    title = getattr(topic, "title", "") or "AI 工作流实操"
    angle = getattr(topic, "content_angle", "") or "教程"
    audience = getattr(topic, "target_audience", "") or "职场人 / AI 新手"
    summary = getattr(topic, "concise_summary", "") or getattr(topic, "raw_summary", "") or title

    title_options = _list(result.get("title_options"))
    cover_options = _list(result.get("cover_text_options"))
    body_text = _text(result.get("body_text"), "")
    hashtags = _list(result.get("hashtags"))
    fact_checks = _list(result.get("fact_checks"))
    risk_tips = _list(result.get("risk_tips"))

    if len(title_options) < 5:
        title_options = (title_options + _default_titles(title, angle, audience))[:5]
    if len(cover_options) < 3:
        cover_options = (cover_options + ["1 小时搭一套流程", "普通人也能照着做", "可复制 AI 提效案例"])[:3]
    if not body_text:
        body_text = _default_body(title, audience, summary)
    if not hashtags:
        hashtags = ["#AI提效", "#AI工作流", "#Agent", "#职场效率", "#小红书图文"]
    if not fact_checks:
        fact_checks = ["核对来源链接和工具名称。", "确认工具能力描述来自官方资料或真实体验。"]
    if not risk_tips:
        risk_tips = ["避免承诺固定涨粉、收入或职业结果。", "AI 生成内容发布前需要人工审核。"]

    return {
        "title_options": title_options[:5],
        "cover_text_options": cover_options[:3],
        "body_text": body_text,
        "hashtags": hashtags[:8],
        "comment_guide": _text(result.get("comment_guide"), "你最想把哪个重复任务交给 AI？评论区告诉我。"),
        "fact_checks": fact_checks[:8],
        "risk_tips": risk_tips[:8],
        "aigc_notice": _text(result.get("aigc_notice"), "本文含 AI 辅助生成内容，已进行人工审核和改写。"),
    }


def normalize_evidence_draft_result(result: dict[str, Any], topic: Any) -> dict[str, Any]:
    """Keep evidence drafts neutral and fail closed when the body is missing.

    Unlike legacy account templates, this path never pads optional choices with
    an unrelated voice or invents prose to make a malformed model result pass.
    Human approval is a later workflow action, never a model-declared fact.
    """
    data = result if isinstance(result, dict) else {}

    def text(key: str) -> str:
        value = data.get(key)
        return value.strip() if isinstance(value, str) else ""

    def choices(key: str, limit: int, max_chars: int) -> list[str]:
        value = data.get(key)
        values = value if isinstance(value, list) else [value] if isinstance(value, str) else []
        clean = [item.strip()[:max_chars] for item in values if isinstance(item, str) and item.strip()]
        return list(dict.fromkeys(clean))[:limit]

    title = str(getattr(topic, "title", "") or "资料整理").strip()[:120] or "资料整理"
    return {
        "title_options": choices("title_options", 5, 120) or [title],
        "cover_text_options": choices("cover_text_options", 3, 120),
        "body_text": text("body_text"),
        "hashtags": choices("hashtags", 8, 80),
        "comment_guide": text("comment_guide"),
        "fact_checks": choices("fact_checks", 8, 1000) or ["请核对事实、引用和资料的适用范围。"],
        "risk_tips": choices("risk_tips", 8, 1000) or ["内容需要人工核验后才能用于公开发布。"],
        "aigc_notice": "内容由 AI 辅助生成，尚待人工审核。",
    }


def normalize_cards_result(result: dict[str, Any], draft: Any) -> list[dict[str, Any]]:
    raw_cards = result.get("cards")
    cards = raw_cards if isinstance(raw_cards, list) else []
    title = _first_list_item(getattr(draft, "title_options", None)) or "AI 工作流实操"
    body = getattr(draft, "body_text", "") or ""

    normalized = []
    for index in range(7):
        raw = cards[index] if index < len(cards) and isinstance(cards[index], dict) else {}
        default = _default_card(index + 1, CARD_TYPES[index], title, body)
        normalized.append({
            "page_index": _int(raw.get("page_index"), index + 1),
            "card_type": _choice(raw.get("card_type"), CARD_TYPES, CARD_TYPES[index]),
            "title": _text(raw.get("title"), default["title"])[:80],
            "subtitle": _text(raw.get("subtitle"), default["subtitle"])[:120],
            "body": _text(raw.get("body"), default["body"])[:260],
            "highlight": _text(raw.get("highlight"), default["highlight"])[:120],
            "footer": _text(raw.get("footer"), "普通人的AI提效实验室")[:80],
            "layout_key": _choice(
                raw.get("layout_key"),
                ["clean_knowledge", "workflow_steps", "tool_review", "pitfall_opinion", "dev_log", "problem_solution", "case_note", "risk_note", "summary"],
                default["layout_key"],
            ),
            "theme_key": _choice(
                raw.get("theme_key"),
                ["lab_clean", "workflow_blue", "warm_note", "deep_work", "notebook"],
                "lab_clean",
            ),
        })
    return normalized


def normalize_compliance_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "risk_level": _choice(result.get("risk_level"), ["low", "medium", "high"], "low"),
        "fact_checks": _list(result.get("fact_checks"))[:8],
        "risk_tips": _list(result.get("risk_tips"))[:8],
        "aigc_notice": _text(result.get("aigc_notice"), "内容含 AI 辅助生成，发布前建议人工审核并按平台规则标识。"),
    }


def _default_titles(title: str, angle: str, audience: str) -> list[str]:
    clean_title = title[:28]
    return [
        f"普通人也能用的{clean_title}",
        f"我把{clean_title}做成了流程",
        f"别只收藏工具，先学会这套{angle}",
        f"用 AI 少做重复劳动：{clean_title}",
        f"给{audience}的 AI 提效实验",
    ]


def _default_body(title: str, audience: str, summary: str) -> str:
    return (
        f"这条内容适合分享给{audience}。\n\n"
        f"主题是：{title}。\n"
        f"核心不是炫工具，而是把真实任务拆成可重复执行的工作流。\n\n"
        f"你可以按 4 步复用：明确目标、整理输入、让 AI 生成结构化初稿、人工审核后记录结果。\n\n"
        f"背景摘要：{summary}"
    )


def _default_card(page_index: int, card_type: str, title: str, body: str) -> dict[str, str]:
    body_hint = (body.replace("\n", " ")[:90] + "...") if len(body) > 90 else body.replace("\n", " ")
    defaults = {
        "cover": ("普通人也能用的 AI 工作流", "从一个真实任务开始", "把复杂工具变成可复用步骤", "少做重复劳动，多做判断和表达", "clean_knowledge"),
        "pain_point": ("痛点不是不会用工具", "而是不知道该让 AI 接哪一步", "很多人直接问最终答案，结果难复用。先拆任务，再让 AI 做局部步骤。", "先拆流程，再调提示词", "problem_solution"),
        "concept": ("一个好工作流长这样", "输入、处理、审核、复盘", "明确输入和输出，再让 AI 生成结构。人工检查事实、语气和风险。", "Agent 是流程，不只是聊天", "clean_knowledge"),
        "workflow": ("4 步复用", "每天 1 小时也能跑", "1. 选重复任务\n2. 写目标限制\n3. 生成可编辑初稿\n4. 审核并记录效果", "每次只优化一个变量", "workflow_steps"),
        "case": ("可以先从这里试", "选择小而高频的任务", body_hint or "从每天重复 10 分钟以上的小任务开始，更容易看到 AI 提效效果。", "小任务最适合启动", "case_note"),
        "pitfall": ("避坑提醒", "别把草稿当成成品", "AI 写得顺不代表事实准确。工具能力、案例和收益描述都要人工核验。", "人工审核是必要环节", "risk_note"),
        "summary": ("今天先记住一句话", "普通人用 AI，不拼工具数量", "先把一个任务跑通，再记录结果，下一次只改一个变量。", "你想让我拆哪个任务？", "summary"),
    }
    title_default, subtitle, body_default, highlight, layout = defaults[card_type]
    return {
        "page_index": str(page_index),
        "card_type": card_type,
        "title": title if card_type == "cover" else title_default,
        "subtitle": subtitle,
        "body": body_default,
        "highlight": highlight,
        "layout_key": layout,
    }


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    return str(value).strip() or default


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace("，", ",").split(",")]
        return [part for part in parts if part]
    return []


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _choice(value: Any, choices: list[str], default: str) -> str:
    text = _text(value)
    return text if text in choices else default


def _first_list_item(value: Any) -> str:
    values = _list(value)
    return values[0] if values else ""

"""发布方案生成器 — 根据内容组合创建新的发布包版本。"""
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.llm.router import router as llm_router
from app.models.card import Card
from app.models.draft import Draft
from app.models.topic import Topic
from app.schemas.draft_variant import DraftVariantGenerateRequest
from app.services.review_lifecycle import has_workflow
from app.services.workflow_support import WorkflowConflict

PROMPT_PATH = Path(__file__).parent.parent / "agents" / "prompts" / "draft_variant_generation.md"

CONTENT_LABELS = {
    "github_project": "GitHub 项目拆解",
    "workflow_tutorial": "AI 工作流教程",
    "pitfall_guide": "避坑指南",
    "tool_review": "工具测评",
    "dev_log": "开发日志",
    "case_study": "案例复盘",
}

TEMPLATE_LABELS = {
    "github_dark": "GitHub拆解风",
    "workflow_clean": "流程卡风",
    "pitfall_alert": "避坑警示风",
    "tool_review_grid": "工具测评风",
    "notebook_warm": "暖白笔记风",
    "business_data": "商业数据风",
}

TEMPLATE_THEMES = {
    "github_dark": "deep_work",
    "workflow_clean": "workflow_blue",
    "pitfall_alert": "warm_note",
    "tool_review_grid": "lab_clean",
    "notebook_warm": "notebook",
    "business_data": "workflow_blue",
}

TEMPLATE_LAYOUTS = {
    "github_dark": "dev_log",
    "workflow_clean": "workflow_steps",
    "pitfall_alert": "risk_note",
    "tool_review_grid": "tool_review",
    "notebook_warm": "case_note",
    "business_data": "clean_knowledge",
}


def _load_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return "你是小红书发布方案生成器，请根据内容组合生成组件化卡片。"


async def generate_draft_variant(draft_id: int, req: DraftVariantGenerateRequest, db: Session) -> tuple[Draft, list[Card], dict]:
    source_draft = db.query(Draft).filter(Draft.id == draft_id).first()
    if not source_draft:
        raise ValueError("草稿不存在")
    if has_workflow(draft_id, db):
        raise WorkflowConflict("此草稿关联 Agent 任务，不能在这里生成独立变体。请返回原任务修改内容并重新提交审核。")
    topic = db.query(Topic).filter(Topic.id == source_draft.topic_id).first()

    max_card_count = max(2, min(req.max_card_count or 7, 7))
    selected_title = _text(req.selected_title) or _first(source_draft.title_options) or (topic.title if topic else "AI 工作流实操")
    selected_cover = _text(req.selected_cover_text) or _first(source_draft.cover_text_options) or "普通人也能照着做"
    content_type = _choice(req.content_type, list(CONTENT_LABELS), _infer_content_type(source_draft, topic))
    template_key = _choice(req.template_key, list(TEMPLATE_LABELS), _template_for_content(content_type))
    theme_key = _text(req.theme_key) or TEMPLATE_THEMES.get(template_key, "lab_clean")
    body_variant_key = _choice(req.body_variant_key, ["first_person", "tutorial_steps"], "first_person")

    requested_provider = req.provider or "local"
    if requested_provider == "local":
        result = _generate_local_variant(
            source_draft,
            topic,
            selected_title,
            selected_cover,
            body_variant_key,
            content_type,
            template_key,
            theme_key,
            max_card_count,
        )
        provider = "local"
        model_name = "local-rule-based-v0"
    else:
        result = await _generate_llm_variant(
            source_draft,
            topic,
            selected_title,
            selected_cover,
            body_variant_key,
            content_type,
            template_key,
            theme_key,
            max_card_count,
            provider=requested_provider,
            model=req.model,
        )
        provider = requested_provider
        model_name = req.model or ""

    result = _normalize_variant_result(
        result,
        source_draft,
        topic,
        selected_title,
        selected_cover,
        body_variant_key,
        content_type,
        template_key,
        theme_key,
        max_card_count,
    )
    variant = result["variant"]
    body_variant_key = variant["body_variant_key"]
    content_type = variant["content_type"]
    template_key = variant["template_key"]
    theme_key = variant["theme_key"]
    max_card_count = variant["max_card_count"]
    body_variants = result["body_variants"]
    if isinstance(req.body_variants, dict):
        for key in ("first_person", "tutorial_steps"):
            text = _text(req.body_variants.get(key))
            if text:
                body_variants[key] = text
    selected_body = body_variants.get(body_variant_key) or body_variants.get("first_person") or source_draft.body_text

    new_draft = Draft(
        topic_id=source_draft.topic_id,
        platform=source_draft.platform,
        title_options=_prefer_first(source_draft.title_options, selected_title),
        cover_text_options=_prefer_first(source_draft.cover_text_options, selected_cover),
        body_text=selected_body,
        hashtags=source_draft.hashtags,
        comment_guide=source_draft.comment_guide,
        fact_checks=source_draft.fact_checks,
        risk_tips=source_draft.risk_tips,
        aigc_notice=source_draft.aigc_notice,
        model_provider=provider,
        model_name=(model_name or "local-rule-based-v0") if provider == "local" else model_name,
        variant_name=variant["variant_name"],
        selected_title=selected_title,
        selected_cover_text=selected_cover,
        body_variant_key=body_variant_key,
        body_variants=body_variants,
        content_type=content_type,
        template_key=template_key,
        theme_key=theme_key,
        max_card_count=max_card_count,
        generated_reason=variant["generated_reason"],
        status="draft",
    )
    db.add(new_draft)
    db.flush()

    cards: list[Card] = []
    for index, item in enumerate(result["cards"], start=1):
        card = Card(
            draft_id=new_draft.id,
            page_index=index,
            card_type=item.get("card_type", "concept"),
            title=_text(item.get("title"))[:255] or selected_title[:255],
            subtitle=_text(item.get("subtitle"))[:255],
            body=_text(item.get("body")),
            highlight=_text(item.get("highlight")),
            footer=_text(item.get("footer")) or "普通人的AI提效实验室",
            layout_key=_text(item.get("layout_key")) or TEMPLATE_LAYOUTS.get(template_key, "clean_knowledge"),
            theme_key=_text(item.get("theme_key")) or theme_key,
            style_json=item.get("style_json") or {"component_key": item.get("component_key", "icon_points")},
        )
        db.add(card)
        cards.append(card)

    if topic:
        topic.status = "generated"
    db.commit()
    db.refresh(new_draft)
    for card in cards:
        db.refresh(card)
    variant_meta = {
        **variant,
        "selected_title": selected_title,
        "selected_cover_text": selected_cover,
        "body_variant_key": body_variant_key,
        "body_variants": body_variants,
        "content_type": content_type,
        "template_key": template_key,
        "theme_key": theme_key,
        "max_card_count": max_card_count,
    }
    return new_draft, cards, variant_meta


def _generate_local_variant(
    draft: Draft,
    topic: Topic | None,
    selected_title: str,
    selected_cover: str,
    body_variant_key: str,
    content_type: str,
    template_key: str,
    theme_key: str,
    max_card_count: int,
) -> dict:
    summary = _summary(draft, topic)
    body_variants = _body_variants(selected_title, summary, content_type)
    chosen_body = body_variants.get(body_variant_key) or body_variants["first_person"]
    variant_name = _variant_name(draft, body_variant_key, template_key)
    reason = _reason(content_type, template_key)
    cards = _cards_for_content(
        selected_title,
        selected_cover,
        chosen_body,
        summary,
        content_type,
        template_key,
        theme_key,
        max_card_count,
    )
    return {
        "variant": {
            "variant_name": variant_name,
            "body_variant_key": body_variant_key,
            "content_type": content_type,
            "template_key": template_key,
            "theme_key": theme_key,
            "max_card_count": max_card_count,
            "generated_reason": reason,
        },
        "body_variants": body_variants,
        "cards": cards,
    }


async def _generate_llm_variant(
    draft: Draft,
    topic: Topic | None,
    selected_title: str,
    selected_cover: str,
    body_variant_key: str,
    content_type: str,
    template_key: str,
    theme_key: str,
    max_card_count: int,
    provider: str,
    model: str = "",
) -> dict:
    client = llm_router.get_task_client("draft_variant_generation", provider=provider or None, model=model or None)
    prompt = _load_prompt()
    user_prompt = f"""选题：{topic.title if topic else selected_title}
摘要：{_summary(draft, topic)}
选中标题：{selected_title}
选中封面：{selected_cover}
正文版本：{body_variant_key}
内容类型：{content_type}
模板风格：{template_key}
主题：{theme_key}
需要生成页数：{max_card_count}
原正文：{(draft.body_text or '')[:1200]}"""
    return client.chat_json(prompt, user_prompt, temperature=0.6)


def _normalize_variant_result(
    result: dict[str, Any],
    draft: Draft,
    topic: Topic | None,
    selected_title: str,
    selected_cover: str,
    body_variant_key: str,
    content_type: str,
    template_key: str,
    theme_key: str,
    max_card_count: int,
) -> dict:
    if not isinstance(result, dict):
        result = {}
    local = _generate_local_variant(
        draft, topic, selected_title, selected_cover, body_variant_key, content_type, template_key, theme_key, max_card_count
    )
    variant = result.get("variant") if isinstance(result.get("variant"), dict) else {}
    body_variants = result.get("body_variants") if isinstance(result.get("body_variants"), dict) else {}
    cards = result.get("cards") if isinstance(result.get("cards"), list) else []

    normalized_variant = {
        "variant_name": _text(variant.get("variant_name")) or local["variant"]["variant_name"],
        "body_variant_key": _choice(variant.get("body_variant_key"), ["first_person", "tutorial_steps"], body_variant_key),
        "content_type": _choice(variant.get("content_type"), list(CONTENT_LABELS), content_type),
        "template_key": _choice(variant.get("template_key"), list(TEMPLATE_LABELS), template_key),
        "theme_key": _text(variant.get("theme_key")) or theme_key,
        "max_card_count": max(2, min(_int(variant.get("max_card_count"), max_card_count), 7)),
        "generated_reason": _text(variant.get("generated_reason")) or local["variant"]["generated_reason"],
    }
    normalized_body_variants = {
        "first_person": _text(body_variants.get("first_person")) or local["body_variants"]["first_person"],
        "tutorial_steps": _text(body_variants.get("tutorial_steps")) or local["body_variants"]["tutorial_steps"],
    }

    normalized_cards = []
    source_cards = cards if cards else local["cards"]
    selected_body = normalized_body_variants.get(body_variant_key) or normalized_body_variants["first_person"]
    source_cards = _ensure_card_count(
        source_cards,
        normalized_variant["max_card_count"],
        selected_title,
        selected_cover,
        selected_body,
        _summary(draft, topic),
        content_type,
    )
    source_cards = _fit_cards_to_count(source_cards, normalized_variant["max_card_count"])
    for index, raw in enumerate(source_cards[:normalized_variant["max_card_count"]], start=1):
        raw = raw if isinstance(raw, dict) else {}
        component_key = _choice(
            raw.get("component_key") or (raw.get("style_json") or {}).get("component_key"),
            ["hero_cover", "icon_points", "flow_steps", "code_block", "compare_table", "checklist", "summary_cta"],
            "icon_points" if index > 1 else "hero_cover",
        )
        style_json = raw.get("style_json") if isinstance(raw.get("style_json"), dict) else {}
        style_json["component_key"] = component_key
        normalized_cards.append({
            "page_index": index,
            "card_type": _text(raw.get("card_type")) or ("cover" if index == 1 else "concept"),
            "component_key": component_key,
            "title": _text(raw.get("title")) or selected_title,
            "subtitle": _text(raw.get("subtitle")),
            "body": _text(raw.get("body")),
            "highlight": _text(raw.get("highlight")),
            "footer": _text(raw.get("footer")) or "普通人的AI提效实验室",
            "layout_key": _text(raw.get("layout_key")) or TEMPLATE_LAYOUTS.get(template_key, "clean_knowledge"),
            "theme_key": _text(raw.get("theme_key")) or theme_key,
            "style_json": style_json,
        })
    return {"variant": normalized_variant, "body_variants": normalized_body_variants, "cards": normalized_cards}


def _body_variants(title: str, summary: str, content_type: str) -> dict[str, str]:
    scenario = _scenario(content_type)
    subject = _subject_line(title, summary)
    example = _example_line(content_type, summary)
    promise = _reader_takeaway(content_type)
    first_person = (
        f"我现在判断「{title}」值不值得做，会先看它能不能解决一个真实的小问题：{subject}。\n\n"
        f"举个例子，{example}。我不会直接让 AI 给最终答案，而是先写清输入资料、想要的输出和必须人工确认的地方。\n\n"
        f"这套方法适合先做成半自动流程：先跑通 1 次，再复盘哪里能复用。{promise}。"
    )
    tutorial = (
        f"你可以把「{title}」按 4 步拆：\n"
        f"1. 先写清它要解决的问题：{subject}。\n"
        f"2. 准备能支撑判断的资料，比如链接、摘要、截图或真实任务背景。\n"
        f"3. 让 AI 输出结构化初稿，包含步骤、例子、风险点，而不是只写漂亮文案。\n"
        f"4. 人工核对事实、表达和边界，再记录发布后的收藏/评论反馈。\n\n"
        f"可以先用这个例子练手：{example}。"
    )
    return {"first_person": first_person, "tutorial_steps": tutorial}


def _cards_for_content(
    selected_title: str,
    selected_cover: str,
    body: str,
    summary: str,
    content_type: str,
    template_key: str,
    theme_key: str,
    max_card_count: int,
) -> list[dict]:
    builders = {
        "github_project": _github_cards,
        "workflow_tutorial": _workflow_cards,
        "pitfall_guide": _pitfall_cards,
        "tool_review": _tool_review_cards,
        "dev_log": _dev_log_cards,
        "case_study": _case_cards,
    }
    requested_count = max(2, min(max_card_count, 7))
    cards = builders.get(content_type, _workflow_cards)(selected_title, selected_cover, body, summary)
    cards = _ensure_card_count(cards, requested_count, selected_title, selected_cover, body, summary, content_type)
    cards = _fit_cards_to_count(cards, requested_count)
    layout_key = TEMPLATE_LAYOUTS.get(template_key, "clean_knowledge")
    for card in cards:
        card["layout_key"] = card.get("layout_key") or layout_key
        card["theme_key"] = theme_key
        card["footer"] = "普通人的AI提效实验室"
        card["style_json"] = {**card.get("style_json", {}), "component_key": card["component_key"]}
    return cards[:requested_count]


def _fit_cards_to_count(cards: list[dict], requested_count: int) -> list[dict]:
    requested_count = max(2, min(requested_count, 7))
    if len(cards) <= requested_count:
        return cards
    cover = cards[0]
    summary = next((card for card in reversed(cards) if card.get("card_type") == "summary"), cards[-1])
    middle = [card for card in cards[1:] if card is not summary]
    if requested_count == 2:
        return [cover, summary]
    return [cover, *middle[: requested_count - 2], summary]


def _ensure_card_count(
    cards: list[dict],
    requested_count: int,
    selected_title: str,
    selected_cover: str,
    body: str,
    summary: str,
    content_type: str,
) -> list[dict]:
    requested_count = max(2, min(requested_count, 7))
    normalized_cards = [card if isinstance(card, dict) else {} for card in cards]
    while len(normalized_cards) < requested_count:
        index = len(normalized_cards) + 1
        normalized_cards.append(_supplement_card(index, requested_count, selected_title, selected_cover, body, summary, content_type))
    return normalized_cards


def _supplement_card(
    index: int,
    requested_count: int,
    selected_title: str,
    selected_cover: str,
    body: str,
    summary: str,
    content_type: str,
) -> dict:
    if index == 1:
        return _card("cover", "hero_cover", selected_title, selected_cover, _cover_body(content_type), _cover_highlight(content_type))
    if index == requested_count:
        return _card(
            "summary",
            "summary_cta",
            "最后怎么行动",
            "给读者一个下一步",
            "先选一个最小任务跑一遍，再把结果记录下来。能复用的部分，再慢慢升级成自己的 Agent。",
            "收藏后照着做",
        )
    templates = {
        "github_project": _card("workflow", "flow_steps", "我会怎么复用", "别只收藏项目", _lines(["先读 README", "提取输入输出", "改成自己的流程"]), "复用比转发重要"),
        "workflow_tutorial": _card("workflow", "checklist", "落地前检查", "别急着自动化", _lines(["输入资料准备好", "输出格式写清楚", "保留人工确认"]), "先稳，再快"),
        "pitfall_guide": _card("pitfall", "checklist", "再补一个检查点", "发布前多问一句", _lines(["事实有没有核对", "有没有夸大收益", "敏感信息有没有脱敏"]), "少踩一个坑就是赚"),
        "tool_review": _card("case", "compare_table", "怎么判断值不值", "回到你的任务", _lines(["看热度|看场景", "看功能|看复用", "看案例|看风险"]), "工具要进流程"),
        "dev_log": _card("workflow", "code_block", "我的复盘 Prompt", "让 Agent 帮你总结", "请总结这次开发解决的问题、关键取舍、验证结果和下次可以复用的步骤。", "复盘会让下一次更快"),
        "case_study": _card("case", "checklist", "你也可以照做", "先准备这些", _lines(["一个重复任务", "一段原始资料", "一个输出模板"]), "案例要能复用"),
    }
    return templates.get(
        content_type,
        _card("concept", "icon_points", "补充说明", "让内容更完整", _lines(["问题是什么", "怎么做更稳", "下一步怎么试"]), "给读者一个抓手"),
    )


def _github_cards(title: str, cover: str, body: str, summary: str) -> list[dict]:
    subject = _subject_line(title, summary)
    example = _example_line("github_project", summary)
    angle = _angle_line(summary)
    return [
        _card("cover", "hero_cover", title, cover, _cover_body("github_project"), _cover_highlight("github_project")),
        _card("concept", "icon_points", "它先解决什么", "别急着看技术栈", _lines([subject, angle, "能否改成自己的输入-处理-输出"]), "先找场景，再看代码"),
        _card("case", "checklist", "适合这几类人", "收藏前先对号入座", _lines(["想学习 Agent 架构", "想找内容选题角度", "想把开源项目改成小工具"]), "能复用才值得收藏"),
        _card("workflow", "flow_steps", "我会这样拆", "把项目变成流程", _lines(["读 README 和示例", "找输入输出", "拆核心模块", "写成自己的使用步骤"]), "别只转发链接"),
        _card("workflow", "code_block", "可以这样问 AI", "先结构化再评价", f"请根据这个项目资料，提取：1）解决的问题；2）适合谁；3）核心模块；4）我能改造成什么工作流。\n背景：{example}", "Prompt 先给框架"),
        _card("pitfall", "checklist", "避坑提醒", "可信比热闹重要", _lines(["不编 stars 和融资数据", "不把 demo 当成熟产品", "保留项目链接和版本信息"]), "宁愿保守，也别乱吹"),
        _card("summary", "summary_cta", "最后记住这句", "开源项目不是新闻", "真正值得发的是：你能把它拆成普通人看得懂、做得到、能复用的工作流。", "你想拆哪个项目？"),
    ]


def _workflow_cards(title: str, cover: str, body: str, summary: str) -> list[dict]:
    subject = _subject_line(title, summary)
    example = _example_line("workflow_tutorial", summary)
    angle = _angle_line(summary)
    return [
        _card("cover", "hero_cover", title, cover, _cover_body("workflow_tutorial"), _cover_highlight("workflow_tutorial")),
        _card("pain_point", "icon_points", "为什么会卡住", "不是不会用 AI", _lines(["目标一句话说不清", "资料散在不同地方", "输出没有统一格式", "少了人工审核点"]), "先拆流程，再写提示词"),
        _card("workflow", "flow_steps", "我会这样拆", "4 步就能启动", _lines(["明确目标", "整理输入", "生成初稿", "审核复盘"]), "每次只优化一步"),
        _card("case", "checklist", "拿它举个例子", subject, _lines([example, angle, "先跑一次最小流程", "看哪里最值得自动化"]), "小任务最适合开始"),
        _card("workflow", "code_block", "可复用 Prompt", "让 AI 先出结构", f"请把这个任务拆成：输入资料、处理步骤、输出格式、人工审核点、复盘指标。\n任务背景：{subject}", "结构比文案更重要"),
        _card("pitfall", "checklist", "别一上来全自动", "先保留人工确认", _lines(["事实要核对", "敏感信息要脱敏", "结果要能修改", "失败样例要记录"]), "半自动更稳"),
        _card("summary", "summary_cta", "今天先跑一遍", "别等完美工具", "选一个每天重复 10 分钟的小任务，把它拆成可复用流程，再决定要不要升级成 Agent。", "你想先自动化哪一步？"),
    ]


def _pitfall_cards(title: str, cover: str, body: str, summary: str) -> list[dict]:
    subject = _subject_line(title, summary)
    example = _example_line("pitfall_guide", summary)
    return [
        _card("cover", "hero_cover", title, cover, _cover_body("pitfall_guide"), _cover_highlight("pitfall_guide")),
        _card("pitfall", "icon_points", "坑 1：直接要答案", "结果往往不可复用", _lines([f"场景没说清：{subject}", "没有输入标准", "没有输出格式", "后面很难复盘"]), "先拆任务"),
        _card("pitfall", "icon_points", "坑 2：忽略审核", "AI 写得顺不等于对", _lines(["工具名可能错", "案例可能虚", "收益容易夸大", "细节一错就掉信任"]), "顺不等于真"),
        _card("case", "checklist", "我会先试这里", "从低风险场景开始", _lines([example, "结果能人工改", "失败也能复盘"]), "别拿高风险任务练手"),
        _card("pitfall", "compare_table", "更稳的做法", "错误/正确对比", _lines(["直接发草稿|人工核对事实", "追求全自动|先跑半自动", "只看工具|先看场景", "只看速度|保留修改余地"]), "先稳，再快"),
        _card("workflow", "checklist", "发布前检查", "照着勾一遍", _lines(["来源保留", "事实核验", "没有绝对承诺", "AIGC 标识"]), "审核是必要环节"),
        _card("summary", "summary_cta", "普通人先记住", "AI 是放大器", "流程没想清，AI 只会把混乱放大。先做小流程，再逐步自动化。", "你踩过哪个坑？"),
    ]


def _tool_review_cards(title: str, cover: str, body: str, summary: str) -> list[dict]:
    subject = _subject_line(title, summary)
    example = _example_line("tool_review", summary)
    return [
        _card("cover", "hero_cover", title, cover, _cover_body("tool_review"), _cover_highlight("tool_review")),
        _card("concept", "icon_points", "我的判断标准", "先看任务，不看热度", _lines([subject, "上手成本能不能接受", "输出能不能进流程", "有没有人工审核空间"]), "适合才值得收藏"),
        _card("case", "checklist", "适合这几类人", "更容易用起来", _lines(["要整理资料的人", "要做内容复盘的人", "要稳定输出结构的人", example]), "先用小任务试"),
        _card("workflow", "flow_steps", "我会这样测", "别只看介绍页", _lines(["准备真实资料", "跑一次输出", "人工核对结果", "记录省了哪一步"]), "工具要进流程"),
        _card("pitfall", "compare_table", "优点和限制", "别只看优点", _lines(["优点|节省整理时间", "限制|需要人工核验", "适合|重复任务", "不适合|高风险决策"]), "先试小任务"),
        _card("summary", "summary_cta", "值不值得用", "我的建议", "如果它能帮你少做重复整理，就值得小范围试；如果只是看起来酷，先别急着换流程。", "你想测哪个工具？"),
    ]


def _dev_log_cards(title: str, cover: str, body: str, summary: str) -> list[dict]:
    subject = _subject_line(title, summary)
    example = _example_line("dev_log", summary)
    return [
        _card("cover", "hero_cover", title, cover, _cover_body("dev_log"), _cover_highlight("dev_log")),
        _card("concept", "icon_points", "这次解决什么", "先说问题，不炫技术", _lines([subject, "为什么现在要改", "改完用户会看到什么", "后面还能复用什么"]), "问题比代码重要"),
        _card("workflow", "flow_steps", "我的开发顺序", "先小步跑通", _lines(["看现有结构", "拆最小功能", "接接口", "做前端验证"]), "别一口吃太大"),
        _card("case", "code_block", "一个关键提示", "让 Agent 先读代码", f"先读取相关文件和交接文档，再按现有架构实现。\n本次目标：{example}", "上下文决定质量"),
        _card("pitfall", "checklist", "这次的坑", "下次要提前检查", _lines(["不要覆盖旧数据", "不要跳过构建", "不要误触发真实模型", "截图验证不能省"]), "验证比想象重要"),
        _card("summary", "summary_cta", "这次学到什么", "普通人也能复用", "把开发过程拆成可复用步骤，本身就是训练 Agent 思维。", "你想看哪段开发？"),
    ]


def _case_cards(title: str, cover: str, body: str, summary: str) -> list[dict]:
    subject = _subject_line(title, summary)
    example = _example_line("case_study", summary)
    return [
        _card("cover", "hero_cover", title, cover, _cover_body("case_study"), _cover_highlight("case_study")),
        _card("case", "icon_points", "场景是什么", "先找真实小问题", _lines([subject, example, "结果格式要稳定", "最好能人工审核"]), "小场景更容易成功"),
        _card("workflow", "flow_steps", "我是这样拆的", "4 步跑通", _lines(["收集资料", "生成结构", "补充例子", "复盘数据"]), "流程比灵感稳定"),
        _card("workflow", "checklist", "你也可以照做", "先准备这些", _lines(["一个重复任务", "一段原始资料", "一个输出模板", "一个复盘指标"]), "先做最小版本"),
        _card("pitfall", "checklist", "注意别踩坑", "案例不能乱编", _lines(["不要夸大收益", "不要假装亲测", "不要忽略风险", "能截图佐证更好"]), "可信比热闹重要"),
        _card("summary", "summary_cta", "最后一句", "普通人用 AI", "先把一个小任务做成流程，再把流程慢慢升级成自己的 Agent。", "你想拆哪个案例？"),
    ]


def _card(card_type: str, component: str, title: str, subtitle: str, body: str, highlight: str) -> dict:
    return {
        "card_type": card_type,
        "component_key": component,
        "title": title,
        "subtitle": subtitle,
        "body": body,
        "highlight": highlight,
    }


def _cover_body(content_type: str) -> str:
    return {
        "github_project": "从问题、适合谁、怎么复用三个角度拆开看",
        "workflow_tutorial": "先跑通一个小流程，再考虑自动化升级",
        "pitfall_guide": "新手最容易翻车的地方，先提前避开",
        "tool_review": "不看热度，先看它能不能进入你的工作流",
        "dev_log": "把一次开发过程拆成可复用的思路",
        "case_study": "从一个小场景开始，拆出能照做的方法",
    }.get(content_type, "把复杂问题拆成普通人能照做的步骤")


def _cover_highlight(content_type: str) -> str:
    return {
        "github_project": "看复用价值，不编造项目数据",
        "workflow_tutorial": "流程清楚，才真的省时间",
        "pitfall_guide": "先避开高频坑，再谈提效",
        "tool_review": "工具要服务场景，不服务热闹",
        "dev_log": "讲清取舍，比堆技术更有价值",
        "case_study": "案例要可信，也要能复用",
    }.get(content_type, "先小范围跑通，再逐步放大")


def _subject_line(title: str, summary: str) -> str:
    clean_summary = _clean_summary(summary)
    if clean_summary:
        return _short_text(clean_summary, 42)
    return _short_text(title, 42) or "一个真实、重复、能复盘的小任务"


def _angle_line(summary: str) -> str:
    clean_summary = _clean_summary(summary)
    if not clean_summary:
        return "重点不是工具有多酷，而是读者能不能照着做"
    return f"这条内容的重点是：{_short_text(clean_summary, 36)}"


def _example_line(content_type: str, summary: str) -> str:
    subject = _subject_line("", summary)
    templates = {
        "github_project": f"把「{subject}」拆成问题、模块和可复用场景",
        "workflow_tutorial": f"拿「{subject}」先跑一个最小流程",
        "pitfall_guide": f"处理「{subject}」时，先保留人工审核和事实核对",
        "tool_review": f"用「{subject}」当测试任务，看它到底省了哪一步",
        "dev_log": f"围绕「{subject}」说明问题、取舍和验证结果",
        "case_study": f"从「{subject}」这个小场景拆出可复用步骤",
    }
    return templates.get(content_type, f"用「{subject}」跑一次小范围验证")


def _reader_takeaway(content_type: str) -> str:
    return {
        "github_project": "读者收藏后能知道怎么拆项目，而不是只得到一个链接",
        "workflow_tutorial": "读者收藏后能照着跑一遍，而不是只觉得很厉害",
        "pitfall_guide": "读者收藏后能少踩一个坑，内容就有价值",
        "tool_review": "读者收藏后能判断自己要不要试，而不是被热度带着走",
        "dev_log": "读者收藏后能复用你的开发方法，而不是只看结果",
        "case_study": "读者收藏后能把案例改成自己的流程",
    }.get(content_type, "读者收藏后能拿去改成自己的小流程")


def _clean_summary(summary: str) -> str:
    text = (summary or "").replace("\r", "\n")
    text = " ".join(part.strip() for part in text.splitlines() if part.strip())
    for prefix in ("摘要：", "总结：", "核心：", "原文："):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


def _short_text(text: str, limit: int) -> str:
    text = _clean_summary(text)
    if not text:
        return ""
    sentence = _first_sentence(text)
    if len(sentence) <= limit:
        return sentence
    return sentence[:limit].rstrip("，。；、 ") + "…"


def _first_sentence(text: str) -> str:
    text = _clean_summary(text)
    if not text:
        return ""
    for sep in ("。", "！", "？", ".", "!", "?"):
        if sep in text:
            part = text.split(sep, 1)[0].strip()
            if part:
                return part
    return text.strip()


def _lines(items: list[str]) -> str:
    return "\n".join(items)


def _infer_content_type(draft: Draft, topic: Topic | None) -> str:
    haystack = " ".join([
        topic.title if topic else "",
        topic.source_type if topic else "",
        topic.raw_summary if topic and topic.raw_summary else "",
        topic.content_angle if topic and topic.content_angle else "",
        draft.body_text or "",
    ]).lower()
    if "github" in haystack or "开源" in haystack or "readme" in haystack:
        return "github_project"
    if "避坑" in haystack or "风险" in haystack or "坑" in haystack:
        return "pitfall_guide"
    if "测评" in haystack or "工具" in haystack or "推荐" in haystack:
        return "tool_review"
    if "日志" in haystack or "开发" in haystack:
        return "dev_log"
    if "案例" in haystack or "复盘" in haystack:
        return "case_study"
    return "workflow_tutorial"


def _template_for_content(content_type: str) -> str:
    return {
        "github_project": "github_dark",
        "workflow_tutorial": "workflow_clean",
        "pitfall_guide": "pitfall_alert",
        "tool_review": "tool_review_grid",
        "dev_log": "notebook_warm",
        "case_study": "business_data",
    }.get(content_type, "workflow_clean")


def _reason(content_type: str, template_key: str) -> str:
    return f"该内容更接近“{CONTENT_LABELS.get(content_type, 'AI 工作流教程')}”，适合使用“{TEMPLATE_LABELS.get(template_key, '流程卡风')}”来突出结构、场景和收藏价值。"


def _scenario(content_type: str) -> str:
    return {
        "github_project": "如果你看到一个 GitHub 项目，不要先转发链接，而是先拆它解决的问题、适合谁、能改造成什么工作流",
        "workflow_tutorial": "如果你每天都要整理会议纪要，可以先把录音转文字、提取行动项、生成待办清单拆开",
        "pitfall_guide": "如果你刚开始用 AI 提效，最容易的问题是直接复制 AI 草稿就发布",
        "tool_review": "如果一个工具看起来很火，我会先拿一个小任务测试它是否真的能省时间",
        "dev_log": "如果我让 Agent 改代码，会先要求它读现有文件，再做一个小功能验证",
        "case_study": "如果你要做一个内容案例，可以先从每天重复出现的小任务开始",
    }.get(content_type, "如果你有一个重复任务，可以先把它拆成输入、处理、输出和人工审核")


def _summary(draft: Draft, topic: Topic | None) -> str:
    parts = [
        topic.concise_summary if topic and topic.concise_summary else "",
        topic.raw_summary if topic and topic.raw_summary else "",
        draft.body_text or "",
        topic.title if topic else "",
    ]
    return next((part for part in parts if part), "AI 工作流提效案例")


def _variant_name(draft: Draft, body_variant_key: str, template_key: str) -> str:
    label = "第一人称" if body_variant_key == "first_person" else "教程步骤"
    return f"方案：{label} + {TEMPLATE_LABELS.get(template_key, '流程卡风')}"


def _prefer_first(values: Any, selected: str) -> list[str]:
    items = values if isinstance(values, list) else []
    clean = [item for item in items if item and item != selected]
    return [selected] + clean[:4]


def _choice(value: Any, choices: list[str], default: str) -> str:
    text = _text(value)
    return text if text in choices else default


def _first(value: Any) -> str:
    if isinstance(value, list) and value:
        return _text(value[0])
    return ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value.strip() if isinstance(value, str) else str(value).strip()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

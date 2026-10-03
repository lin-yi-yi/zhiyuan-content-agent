"""发布包质量评分服务 — PackageEvaluator (local 规则 + LLM)"""
import re
from pathlib import Path

from app.models.draft import Draft
from app.models.card import Card
from app.schemas.evaluation import EvaluationOut, Issue, Scores
from sqlalchemy.orm import Session

PROMPT_PATH = Path(__file__).parent.parent / "agents" / "prompts" / "package_evaluation.md"


def _load_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return "你是小红书内容质量评审员，请评估发布包质量。"


def evaluate_local(draft: Draft, cards: list[Card]) -> dict:
    """本地规则评分（无需 LLM，快速兜底）"""
    scores = Scores()
    issues = []
    strengths = []
    suggestions = []

    # 1. 封面钩子强度 (0-15)
    if cards and cards[0].title:
        t = cards[0].title
        if len(t) >= 10 and len(t) <= 25:
            scores.title_hook = 12
            strengths.append("封面标题长度合适")
        elif len(t) < 8:
            scores.title_hook = 6
            issues.append(Issue(level="medium", card_page=1, message="封面标题太短，建议 10-25 字"))
        else:
            scores.title_hook = 10
        if any(kw in t for kw in ["省", "倍", "元", "分钟", "小时", "步骤", "一个"]):
            scores.title_hook = min(15, scores.title_hook + 3)
    else:
        scores.title_hook = 5
        issues.append(Issue(level="high", card_page=1, message="封面标题缺失"))

    # 2. 小红书适配度 (0-15)
    xhs_score = 10
    if draft.body_text:
        body = draft.body_text
        if len(body) < 100:
            xhs_score -= 3
            issues.append(Issue(level="medium", card_page=None, message="正文过短，小红书图文建议 200-600 字"))
        elif len(body) > 1000:
            xhs_score -= 2
            issues.append(Issue(level="low", card_page=None, message="正文偏长，建议精简到 800 字以内"))
        if draft.hashtags and len(draft.hashtags) >= 3:
            xhs_score += 2
        else:
            issues.append(Issue(level="low", card_page=None, message="标签不足 3 个，建议补到 5-8 个"))
        xhs_score = max(0, min(15, xhs_score))
    scores.xiaohongshu_fit = xhs_score

    # 3. 收藏价值 (0-15)
    collect = 8
    workflow_cards = [c for c in cards if c.card_type in ("workflow", "case")]
    if len(workflow_cards) >= 2:
        collect += 4
        strengths.append("有足够的工作流和案例页，收藏价值较高")
    elif len(workflow_cards) == 1:
        collect += 2
    else:
        issues.append(Issue(level="medium", card_page=None, message="缺少工作流步骤或案例页，收藏价值偏低"))
    for c in cards:
        if c.body and any(kw in c.body for kw in ["步骤", "打开", "点击", "输入", "配置", "复制", "粘贴"]):
            collect += 2
            break
    scores.collectability = min(15, collect)

    # 4. 普通人可理解度 (0-15)
    clarity = 10
    for c in cards:
        if c.body and len(c.body) > 120:
            clarity -= 1
    if draft.body_text:
        jargon = ["transformer", "embedding", "fine-tun", "RLHF", "SFT", "attention", "tokenization"]
        for j in jargon:
            if j.lower() in draft.body_text.lower():
                clarity -= 1
    scores.clarity = max(5, min(15, clarity))

    # 5. 工作流实用性 (0-10)
    workflow_score = 6
    if any(c.card_type == "workflow" for c in cards):
        workflow_score += 2
    body = draft.body_text or ""
    if any(kw in body for kw in ["步骤", "操作", "流程", "方法", "怎么做"]):
        workflow_score += 2
    scores.workflow_usability = min(10, workflow_score)

    # 6. 卡片节奏 (0-10)
    if any(card.card_type == "cover" for card in cards) and any(
        card.card_type != "cover" and card.body for card in cards
    ):
        scores.card_rhythm = 8
        strengths.append("具备封面和内容页，页数按实际资料组织")
    elif cards:
        scores.card_rhythm = 5
        issues.append(Issue(level="medium", card_page=None, message="请检查是否包含封面和有实际内容的正文页"))
    else:
        scores.card_rhythm = 3
        issues.append(Issue(level="high", card_page=None, message="卡片数量不足，缺少完整节奏"))
    # 检查文字密度
    for c in cards:
        if c.body and len(c.body) > 150:
            scores.card_rhythm = max(0, scores.card_rhythm - 1)
            issues.append(Issue(level="low", card_page=c.page_index, message=f"第{c.page_index}页文字偏多，建议精简"))

    # 7. 事实和合规风险 (0-10)
    risk = 8
    if draft.risk_tips and len(draft.risk_tips) > 0:
        risk += 1
    if draft.fact_checks and len(draft.fact_checks) > 0:
        risk += 1
    body_lower = body.lower()
    risk_keywords = ["一定", "肯定", "100%", "绝对", "保证", "必定", "包你"]
    for kw in risk_keywords:
        if kw in body_lower:
            risk -= 2
            issues.append(Issue(level="high", card_page=None, message=f"正文包含绝对化表达「{kw}」，建议改为客观描述"))
            break
    scores.factual_risk = max(0, min(10, risk))
    if scores.factual_risk < 6:
        issues.append(Issue(level="high", card_page=None, message="合规风险较高，发布前需要人工确认事实和表达"))

    # 8. 评论引导 (0-5)
    if draft.comment_guide and len(draft.comment_guide) > 10:
        guide = draft.comment_guide
        if "?" in guide or "？" in guide:
            scores.comment_guide = 5
            strengths.append("评论引导包含提问，有助于引发讨论")
        else:
            scores.comment_guide = 3
    else:
        scores.comment_guide = 2
        suggestions.append("建议添加评论引导语，例如「你最想自动化的重复任务是什么？」")

    # 9. AIGC 准备度 (0-5)
    if draft.aigc_notice and len(draft.aigc_notice) > 5:
        scores.aigc_readiness = 5
    else:
        scores.aigc_readiness = 3
        suggestions.append("建议补上 AIGC 标识说明")

    # 综合
    overall = sum([
        scores.title_hook, scores.xiaohongshu_fit, scores.collectability,
        scores.clarity, scores.workflow_usability, scores.card_rhythm,
        scores.factual_risk, scores.comment_guide, scores.aigc_readiness,
    ])

    high_issues = [i for i in issues if i.level == "high"]
    readiness = "ready" if overall >= 75 and not high_issues else "needs_review"
    if overall < 50:
        readiness = "not_ready"

    if overall >= 75:
        strengths.append("整体质量较好，经过人工审核后可发布")

    return EvaluationOut(
        overall_score=overall,
        publish_readiness=readiness,
        scores=scores,
        strengths=strengths[:5],
        issues=issues[:8],
        rewrite_suggestions=suggestions[:5],
    ).model_dump()


async def evaluate_with_llm(draft: Draft, cards: list[Card], provider: str = "deepseek", model: str = "") -> dict:
    """LLM 评分（真实模型，结果更深入）"""
    from app.llm.router import router as llm_router

    system_prompt = _load_prompt()

    cards_text = "\n".join([
        f"第{c.page_index}页[{c.card_type}] 标题:{c.title} 副标题:{c.subtitle or ''} 正文:{c.body or ''} 强调:{c.highlight or ''}"
        for c in cards
    ])

    user_prompt = f"""请对这套小红书发布包进行质量评分：

标题候选：{draft.title_options[0] if draft.title_options else '无'}
正文：{draft.body_text[:600] if draft.body_text else '无'}
标签：{draft.hashtags or []}
评论引导：{draft.comment_guide or '无'}
事实核验：{draft.fact_checks or []}
风险提示：{draft.risk_tips or []}

卡片内容：
本次实际为 {len(cards)} 页，请按信息完整性和证据覆盖评估，不要求固定页数，不为凑页数添加无来源内容。
{cards_text[:3000]}"""

    client = llm_router.get_task_client("package_evaluation", provider=provider or None, model=model or None)
    result = client.chat_json(system_prompt, user_prompt, temperature=0.3)

    # 确保结构完整
    if "scores" not in result:
        result["scores"] = {}
    if "issues" not in result:
        result["issues"] = []
    if "strengths" not in result:
        result["strengths"] = []
    if "rewrite_suggestions" not in result:
        result["rewrite_suggestions"] = []
    if "overall_score" not in result:
        result["overall_score"] = sum(result["scores"].values()) if result["scores"] else 50
    if "publish_readiness" not in result:
        result["publish_readiness"] = "needs_review"

    return result


async def evaluate_draft(draft: Draft, cards: list[Card], db: Session,
                         provider: str = "local", model: str = "") -> dict:
    """评估发布包质量。provider=local 时使用规则评分，否则调用 LLM"""
    if provider == "local":
        return evaluate_local(draft, cards)
    try:
        return await evaluate_with_llm(draft, cards, provider=provider, model=model or None)
    except Exception as e:
        # LLM 失败时降级到 local
        result = evaluate_local(draft, cards)
        result["_fallback"] = f"LLM 评分失败({str(e)[:80]})，已使用规则评分"
        return result

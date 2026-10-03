"""本地规则模型客户端。

用于没有 API Key 时跑通 MVP 工作流：选题评分、发布包生成、卡片生成和合规检查。
"""
import json
import hashlib
import re
import time

from app.llm.base import BaseLLMClient
from app.llm.tracing import model_request_metadata, persist_model_run


class LocalRuleBasedClient(BaseLLMClient):
    provider = "local"
    model = "local-rule-based-v0"

    def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.7, max_tokens: int = 4096) -> str:
        start = time.time()
        if "连接成功" in user_prompt:
            content = "连接成功。本地规则模型已就绪，可以用于离线自测。"
        else:
            content = "这是本地规则模型的回复，用于在未配置 API Key 时跑通工作台流程。"
        self._log_run("chat", system_prompt + user_prompt, content, True, start, system_prompt=system_prompt)
        return content

    def chat_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> dict:
        start = time.time()
        prompt = system_prompt + "\n" + user_prompt
        if "小红书发布包生成" in system_prompt:
            generate, task_type = self._generate_draft, "draft_generation"
        elif "选题评分" in system_prompt:
            generate, task_type = self._score_topic, "topic_score"
        elif "卡片生成" in system_prompt:
            generate, task_type = self._generate_cards, "card_generation"
        elif "合规检查" in system_prompt:
            generate, task_type = self._check_compliance, "compliance_check"
        else:
            generate, task_type = self._generate_draft, "draft_generation"

        try:
            result = generate(user_prompt)
        except Exception as exc:
            self._log_run(task_type, prompt, "", False, start, system_prompt=system_prompt,
                          error_type=type(exc).__name__[:100])
            raise

        self._log_run(task_type, prompt, json.dumps(result, ensure_ascii=False), True, start, system_prompt=system_prompt)
        return result

    def _score_topic(self, text: str) -> dict:
        title = self._field(text, "标题") or "未命名选题"
        source_type = self._field(text, "来源类型") or "manual"
        summary = self._field(text, "原始摘要") or title
        haystack = f"{title} {summary}".lower()

        score = 58
        positives = [
            ("agent", 8), ("工作流", 10), ("提效", 8), ("自动化", 7),
            ("开源", 6), ("github", 6), ("实操", 8), ("案例", 7),
            ("教程", 6), ("复盘", 5),
        ]
        for keyword, points in positives:
            if keyword.lower() in haystack:
                score += points
        if source_type in {"github", "official_blog"}:
            score += 4
        if not summary or summary == "无":
            score -= 8

        risky_terms = ["医疗", "法律", "投资", "保证", "暴富", "账号密码", "cookie"]
        risk_hits = [term for term in risky_terms if term.lower() in haystack]
        score -= min(18, len(risk_hits) * 6)
        score = max(35, min(92, score))

        if "github" in haystack or "开源" in haystack:
            angle = "案例拆解"
            audience = "开发者 / AI 新手"
        elif "避坑" in haystack or "风险" in haystack:
            angle = "避坑"
            audience = "职场人 / 自媒体人"
        elif "agent" in haystack:
            angle = "开发日志"
            audience = "AI 新手 / 职场人"
        else:
            angle = "教程"
            audience = "职场人 / AI 新手"

        risk_level = "high" if risk_hits else "low"
        platform = "小红书图文" if score >= 60 and risk_level == "low" else "暂不推荐"
        concise = self._make_concise_summary(title, summary)

        return {
            "concise_summary": concise,
            "target_audience": audience,
            "content_angle": angle,
            "recommended_platform": platform,
            "score": score,
            "score_reason": (
                f"本选题与账号主线匹配度较高，适合用步骤、截图和案例做成图文。"
                f"主要加分点是实用性和图文化表达潜力；风险等级为{risk_level}。"
            ),
            "risk_level": risk_level,
        }

    def _generate_draft(self, text: str) -> dict:
        title = self._field(text, "选题标题") or self._field(text, "选题") or "AI 工作流实操"
        angle = self._field(text, "选题角度") or "教程"
        audience = self._field(text, "目标人群") or "职场人"
        summary = self._field(text, "摘要") or title
        core = self._clean_title(title)

        return {
            "title_options": [
                f"普通人也能用的 {core}",
                f"我把 {core} 做成了一个可复用流程",
                f"别只收藏工具了，先学会这套 {angle} 方法",
                f"用 AI 少做重复劳动：{core}",
                f"给 {audience} 的 AI 提效实验：{core}",
            ],
            "cover_text_options": [
                "1 小时搭一套可复用流程",
                "普通人也能照着做",
                "从选题到复盘的实操卡片",
            ],
            "body_text": (
                f"这条内容适合分享给{audience}。\n\n"
                f"我这次拆的是：{core}。\n"
                f"核心不是炫工具，而是把一个真实任务拆成可重复执行的工作流：先明确输入，"
                f"再让 AI 生成初稿，最后人工审核事实、语气和风险。\n\n"
                f"你可以按这 4 步复用：\n"
                f"1. 写清楚任务目标和约束。\n"
                f"2. 让 AI 输出结构化步骤，而不是直接要最终答案。\n"
                f"3. 用自己的判断补上案例、截图或真实使用场景。\n"
                f"4. 记录结果，下一次只优化表现最差的一步。\n\n"
                f"如果你也在做 AI 提效，可以先从一个每天重复 10 分钟以上的小任务开始。"
            ),
            "hashtags": ["#AI提效", "#AI工作流", "#Agent", "#职场效率", "#普通人的AI提效实验室"],
            "comment_guide": "你最想把哪个重复工作交给 AI？评论区告诉我，我下次拿真实任务拆一遍。",
            "fact_checks": [
                "确认来源链接和项目名称是否准确。",
                "确认所有工具能力描述来自实际体验或官方资料。",
            ],
            "risk_tips": [
                "避免承诺固定涨粉、变现或薪资结果。",
                "涉及 AI 生成内容时建议保留人工审核和 AIGC 标识。",
            ],
            "aigc_notice": "本文含 AI 辅助生成内容，已进行人工审核和改写。",
        }

    def _generate_cards(self, text: str) -> dict:
        title = self._field(text, "选题") or "AI 工作流实操"
        body = self._field(text, "正文") or title
        core = self._clean_title(title)
        body_hint = self._trim(body, 90)
        return {
            "cards": [
                {
                    "page_index": 1,
                    "card_type": "cover",
                    "title": core,
                    "subtitle": "普通人的 AI 提效实验室",
                    "body": "把一个真实任务拆成可复用工作流",
                    "highlight": "少做重复劳动，多做判断和表达",
                    "layout_key": "clean_knowledge",
                    "theme_key": "lab_clean",
                },
                {
                    "page_index": 2,
                    "card_type": "pain_point",
                    "title": "痛点不是不会用工具",
                    "subtitle": "而是不知道该让 AI 接哪一步",
                    "body": "很多人一上来就问最终答案，结果很难复用。更稳的做法是先拆任务，再给 AI 明确输入和输出格式。",
                    "highlight": "先拆流程，再调提示词",
                    "layout_key": "problem_solution",
                    "theme_key": "lab_clean",
                },
                {
                    "page_index": 3,
                    "card_type": "concept",
                    "title": "一个好工作流长这样",
                    "subtitle": "输入、处理、审核、复盘",
                    "body": "输入资料和目标；让 AI 生成结构；人工检查事实和风险；发布后记录数据，再优化下一次。",
                    "highlight": "Agent 是流程，不只是聊天",
                    "layout_key": "clean_knowledge",
                    "theme_key": "lab_clean",
                },
                {
                    "page_index": 4,
                    "card_type": "workflow",
                    "title": "4 步复用",
                    "subtitle": "每天 1 小时也能跑",
                    "body": "1. 选一个重复任务\n2. 写清楚目标和限制\n3. 让 AI 输出可编辑初稿\n4. 人工审核后记录效果",
                    "highlight": "把 AI 放在流程里，而不是替你思考全部",
                    "layout_key": "workflow_steps",
                    "theme_key": "workflow_blue",
                },
                {
                    "page_index": 5,
                    "card_type": "case",
                    "title": "可以先从这里试",
                    "subtitle": "内容创作任务最适合入门",
                    "body": body_hint,
                    "highlight": "选择小而高频的任务，成功率最高",
                    "layout_key": "case_note",
                    "theme_key": "lab_clean",
                },
                {
                    "page_index": 6,
                    "card_type": "pitfall",
                    "title": "避坑提醒",
                    "subtitle": "别把草稿当成成品",
                    "body": "AI 容易写得很顺，但事实、案例、语气和平台风险仍要人工检查。尤其不要夸大收益。",
                    "highlight": "人工审核是护城河的一部分",
                    "layout_key": "risk_note",
                    "theme_key": "warm_note",
                },
                {
                    "page_index": 7,
                    "card_type": "summary",
                    "title": "今天先记住一句话",
                    "subtitle": "普通人用 AI，不拼工具数量",
                    "body": "先把一个任务跑通，再记录结果，下一次只改一个变量。这样你的 AI 能力会真的长出来。",
                    "highlight": "你想让我拆哪个职场任务？",
                    "layout_key": "summary",
                    "theme_key": "lab_clean",
                },
            ],
        }

    def _check_compliance(self, text: str) -> dict:
        risky_terms = ["保证", "暴富", "医疗", "法律", "投资", "cookie", "账号密码"]
        hits = [term for term in risky_terms if term.lower() in text.lower()]
        return {
            "risk_level": "high" if hits else "low",
            "fact_checks": [
                "核对工具名称、项目名称和来源链接。",
                "确认案例描述来自真实体验或明确标注为假设场景。",
            ],
            "risk_tips": [
                "不要承诺固定涨粉、收入或职业结果。",
                "不要引导用户提交账号密码、Cookie 或敏感凭证。",
            ] + ([f"文本中出现高风险词：{', '.join(hits)}。"] if hits else []),
            "aigc_notice": "内容含 AI 辅助生成，发布前建议人工审核并按平台规则标识。",
        }

    def _log_run(self, task_type: str, input_text: str, output_text: str, success: bool, start: float,
                 *, system_prompt: str = "", error_type: str | None = None):
        latency_ms = int((time.time() - start) * 1000)
        persist_model_run(task_type=task_type, provider=self.provider, model_name=self.model,
            prompt_hash=hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
            success=success, latency_ms=latency_ms,
            request_metadata=model_request_metadata(system_prompt, "local_rule"), error_type=error_type)

    @staticmethod
    def _field(text: str, label: str) -> str:
        for line in text.splitlines():
            if line.startswith(f"{label}："):
                return line.split("：", 1)[1].strip()
        return ""

    @staticmethod
    def _clean_title(title: str) -> str:
        title = re.sub(r"^[#\d\.\s]+", "", title).strip()
        return title[:28] or "AI 工作流实操"

    @staticmethod
    def _trim(text: str, limit: int) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit] + ("..." if len(text) > limit else "")

    def _make_concise_summary(self, title: str, summary: str) -> str:
        base = summary if summary and summary != "无" else title
        return self._trim(base, 90)

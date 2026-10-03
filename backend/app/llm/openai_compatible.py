"""OpenAI 兼容客户端"""
import json
import hashlib
import re
import time

from openai import OpenAI

from app.llm.base import BaseLLMClient
from app.llm.tracing import model_invocation_context, model_request_metadata, persist_model_run


class ModelCallError(RuntimeError):
    """Sanitized provider error safe to show or store outside the client."""

    def __init__(self, error_type: str, *, response_format_unsupported: bool = False):
        super().__init__("模型服务调用失败，请检查配置或稍后重试")
        self.error_type = error_type
        self.response_format_unsupported = response_format_unsupported


class OpenAICompatibleClient(BaseLLMClient):
    def __init__(self, api_key: str, base_url: str, model: str, provider: str = "unknown"):
        self.provider = provider
        self.model = model
        # One log row is one application request; no hidden SDK network retries.
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0, max_retries=0)

    def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.7, max_tokens: int = 4096) -> str:
        return self.chat_with_format(system_prompt, user_prompt, temperature, max_tokens)

    def chat_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> dict:
        with model_invocation_context():
            return self._chat_json(system_prompt, user_prompt, temperature)

    def _chat_json(self, system_prompt: str, user_prompt: str, temperature: float) -> dict:
        prompt_suffix = "\n\n请严格输出一个合法 JSON 对象，不要 Markdown，不要解释。"
        full_user_prompt = user_prompt + prompt_suffix
        call_kind = "initial"
        for attempt in range(3):
            try:
                response_format = "json_object" if attempt == 0 else "text"
                text = self.chat_with_format(system_prompt, full_user_prompt, temperature,
                                             response_format=response_format, call_kind=call_kind)
                return self._parse_json_object(text)
            except Exception as e:
                if attempt == 0 and self._looks_like_response_format_error(e):
                    call_kind = "format_fallback"
                    continue
                if isinstance(e, ModelCallError):
                    raise
                if attempt == 2:
                    break

                repair_prompt = (
                    "下面这段内容没有被解析成合法 JSON。请只返回修复后的 JSON 对象，"
                    "不要添加解释，不要使用 Markdown。\n\n"
                    f"原始内容：\n{locals().get('text', '')[:6000]}\n\n"
                    f"解析错误类型：{type(e).__name__}"
                )
                try:
                    text = self.chat_with_format(
                        "你是 JSON 修复器，负责把文本修复为合法 JSON 对象。",
                        repair_prompt,
                        temperature=0,
                        response_format="text",
                        call_kind="json_repair",
                    )
                    return self._parse_json_object(text)
                except Exception as repair_error:
                    if isinstance(repair_error, ModelCallError):
                        raise
                call_kind = "json_retry"

        raise ValueError("模型没有返回合法 JSON 对象") from None

    @staticmethod
    def _parse_json_object(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', text)
            if not match:
                raise
            value = json.loads(match.group())
        if not isinstance(value, dict):
            raise ValueError("模型 JSON 输出必须是对象")
        return value

    @staticmethod
    def _looks_like_response_format_error(error: Exception) -> bool:
        if isinstance(error, ModelCallError):
            return error.response_format_unsupported
        message = str(error).lower()
        return "response_format" in message or "json_object" in message

    def chat_with_format(self, system_prompt: str, user_prompt: str,
                         temperature: float = 0.7, max_tokens: int = 4096,
                         response_format: str = "text", call_kind: str = "initial") -> str:
        from app.saas.context import is_saas_mode
        if is_saas_mode():
            from app.saas.connections import enforce_provider
            enforce_provider(self.provider)
        start = time.perf_counter()
        request_metadata = model_request_metadata(system_prompt, call_kind)
        prompt_hash = hashlib.sha256(json.dumps(
            [["system", system_prompt], ["user", user_prompt]],
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if response_format == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
            latency = int((time.perf_counter() - start) * 1000)
            content = resp.choices[0].message.content or ""
            self._log_run("chat", prompt_hash, True, latency, usage=getattr(resp, "usage", None),
                          request_metadata=request_metadata)
            return content
        except Exception as e:
            latency = int((time.perf_counter() - start) * 1000)
            error_type = type(e).__name__[:100]
            self._log_run("chat", prompt_hash, False, latency, error_type=error_type,
                          request_metadata=request_metadata)
            raise ModelCallError(
                error_type, response_format_unsupported=self._looks_like_response_format_error(e),
            ) from None

    def _log_run(self, task_type: str, prompt_hash: str,
                 success: bool, latency_ms: int, usage=None, error_type: str | None = None,
                 request_metadata: dict | None = None):
        """Persist metadata only; prompts, responses and provider error bodies stay out."""
        persist_model_run(task_type=task_type, provider=self.provider, model_name=self.model,
            prompt_hash=prompt_hash, success=success, latency_ms=latency_ms, usage=usage,
            error_type=error_type, request_metadata=request_metadata or {})

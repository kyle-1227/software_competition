from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.config import settings

try:  # Optional dependency in the development environment.
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional dependency may be absent.
    ChatOpenAI = None  # type: ignore[assignment]


@dataclass
class DeepSeekLLMResponse:
    text: str
    model: str | None
    usage: dict[str, Any] | None
    warnings: list[str]


class DeepSeekLLMClient:
    """DeepSeek V4 client with fallback-first behavior.

    The client never raises raw provider errors to the harness. It filters
    reasoning fields and falls back to deterministic text when the API is not
    configured or unavailable.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        thinking_enabled: bool | None = None,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = base_url or settings.deepseek_base_url
        self.model = model or settings.deepseek_model
        self.thinking_enabled = (
            settings.deepseek_thinking_enabled
            if thinking_enabled is None
            else thinking_enabled
        )
        self.reasoning_effort = reasoning_effort or settings.deepseek_reasoning_effort
        self.temperature = (
            settings.deepseek_temperature if temperature is None else temperature
        )
        self.max_tokens = max_tokens or settings.deepseek_max_tokens
        self._client = self._build_client()

    def _build_client(self):
        if not self.api_key or ChatOpenAI is None:
            return None
        try:
            return ChatOpenAI(
                model=self.model,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception:
            return None

    async def generate_text(self, prompt: str, context: dict[str, Any] | None = None):
        if self._client is None:
            return self._fallback_text(prompt, context)

        warnings: list[str] = []
        response = await self._invoke_text(prompt, context, use_thinking=True)
        if response is None:
            warnings.append("DeepSeek thinking 调用失败，已降级为不带 thinking 重试。")
            response = await self._invoke_text(prompt, context, use_thinking=False)
        if response is None:
            warnings.append("DeepSeek 调用失败，已降级为 deterministic fallback。")
            fallback = self._fallback_text(prompt, context)
            fallback.warnings.extend(warnings)
            return fallback
        response.warnings.extend(warnings)
        return response

    async def generate_json(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> DeepSeekLLMResponse:
        if self._client is None:
            return self._fallback_json(prompt, context)

        warnings: list[str] = []
        response = await self._invoke_json(prompt, context, use_thinking=True)
        if response is None:
            warnings.append("DeepSeek thinking JSON 调用失败，已降级重试。")
            response = await self._invoke_json(prompt, context, use_thinking=False)
        if response is None:
            warnings.append("DeepSeek JSON 调用失败，已降级为 deterministic fallback。")
            fallback = self._fallback_json(prompt, context)
            fallback.warnings.extend(warnings)
            return fallback
        response.warnings.extend(warnings)
        return response

    async def _invoke_text(
        self,
        prompt: str,
        context: dict[str, Any] | None,
        use_thinking: bool,
    ) -> DeepSeekLLMResponse | None:
        try:
            messages = self._build_messages(prompt, context)
            response = await self._client.ainvoke(
                messages,
                extra_body=self._extra_body(use_thinking),
            )
            text = self._extract_text(response)
            return DeepSeekLLMResponse(
                text=text,
                model=self._extract_model(response),
                usage=self._extract_usage(response),
                warnings=[],
            )
        except Exception:
            return None

    async def _invoke_json(
        self,
        prompt: str,
        context: dict[str, Any] | None,
        use_thinking: bool,
    ) -> DeepSeekLLMResponse | None:
        try:
            messages = self._build_messages(prompt, context)
            response = await self._client.ainvoke(
                messages,
                extra_body=self._extra_body(use_thinking),
                response_format={"type": "json_object"},
            )
            text = self._extract_text(response)
            data = json.loads(text)
            filtered = json.dumps(self._filter_sensitive_fields(data), ensure_ascii=False)
            return DeepSeekLLMResponse(
                text=filtered,
                model=self._extract_model(response),
                usage=self._extract_usage(response),
                warnings=[],
            )
        except Exception:
            return None

    def _extra_body(self, use_thinking: bool) -> dict[str, Any]:
        thinking_enabled = self.thinking_enabled and use_thinking
        return {
            "thinking": {"type": "enabled" if thinking_enabled else "disabled"},
            "reasoning_effort": self.reasoning_effort,
        }

    def _build_messages(self, prompt: str, context: dict[str, Any] | None):
        if context:
            return [
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False),
                },
            ]
        return [{"role": "user", "content": prompt}]

    def _extract_text(self, response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(str(item) for item in content)
        if isinstance(content, str):
            return self._filter_reasoning_text(content)
        return self._filter_reasoning_text(str(content))

    def _extract_model(self, response: Any) -> str | None:
        metadata = getattr(response, "response_metadata", {})
        model = metadata.get("model") if isinstance(metadata, dict) else None
        return model or self.model

    def _extract_usage(self, response: Any) -> dict[str, Any] | None:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            metadata = getattr(response, "response_metadata", {})
            usage = metadata.get("token_usage") if isinstance(metadata, dict) else None
        return usage if isinstance(usage, dict) else None

    def _filter_sensitive_fields(self, data: Any) -> Any:
        if isinstance(data, dict):
            forbidden = {
                "reasoning_content",
                "reasoning",
                "chain_of_thought",
                "thinking",
            }
            return {
                key: self._filter_sensitive_fields(value)
                for key, value in data.items()
                if key not in forbidden
            }
        if isinstance(data, list):
            return [self._filter_sensitive_fields(item) for item in data]
        return data

    def _filter_reasoning_text(self, text: str) -> str:
        return text.replace("reasoning_content", "").replace("chain_of_thought", "")

    def _fallback_text(
        self, prompt: str, context: dict[str, Any] | None
    ) -> DeepSeekLLMResponse:
        del prompt
        summary = json.dumps(context or {}, ensure_ascii=False)
        return DeepSeekLLMResponse(
            text=f"当前使用 deterministic fallback。上下文摘要：{summary}",
            model=self.model,
            usage=None,
            warnings=["DeepSeek 未配置或不可用，已使用 fallback。"],
        )

    def _fallback_json(
        self, prompt: str, context: dict[str, Any] | None
    ) -> DeepSeekLLMResponse:
        del prompt
        payload = self._filter_sensitive_fields(context or {})
        return DeepSeekLLMResponse(
            text=json.dumps(payload, ensure_ascii=False),
            model=self.model,
            usage=None,
            warnings=["DeepSeek 未配置或不可用，已使用 fallback。"],
        )

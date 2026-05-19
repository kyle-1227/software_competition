from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.model_gateway.providers.deepseek import DeepSeekProvider
from app.model_gateway.schemas import ModelGatewayResponse, ModelRequest, ModelTask


class ModelGateway:
    """Single runtime entrypoint for model-backed capabilities.

    The gateway keeps the old generate_text/generate_json shape so existing
    guardrails, evaluators, tools, and tests can migrate without a flag day.
    """

    def __init__(self, deepseek_client: Any | None = None) -> None:
        self._provider = (
            DeepSeekProvider(deepseek_client)
            if deepseek_client is not None
            else None
        )
        self.model = getattr(deepseek_client, "model", None) or getattr(
            settings, "deepseek_model", None
        )

    async def generate_text(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
        *,
        task: ModelTask = "generic",
    ) -> ModelGatewayResponse:
        request = ModelRequest(
            prompt=prompt,
            context=context or {},
            task=task,
            response_format="text",
        )
        return await self.invoke(request)

    async def generate_json(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
        *,
        task: ModelTask = "generic",
    ) -> ModelGatewayResponse:
        request = ModelRequest(
            prompt=prompt,
            context=context or {},
            task=task,
            response_format="json",
        )
        return await self.invoke(request)

    async def invoke(self, request: ModelRequest) -> ModelGatewayResponse:
        if self._provider is None:
            return self._fallback_response(request)

        try:
            if request.response_format == "json":
                response = await self._provider.generate_json(
                    request.prompt,
                    request.context,
                )
            else:
                response = await self._provider.generate_text(
                    request.prompt,
                    request.context,
                )
        except Exception as exc:
            fallback = self._fallback_response(request)
            fallback.warnings.append(f"model_gateway provider failed: {exc}")
            return fallback

        return self._normalize_response(response, request)

    def _normalize_response(
        self,
        response: Any,
        request: ModelRequest,
    ) -> ModelGatewayResponse:
        warnings = getattr(response, "warnings", [])
        if not isinstance(warnings, list):
            warnings = [str(warnings)]
        return ModelGatewayResponse(
            text=str(getattr(response, "text", "") or ""),
            model=getattr(response, "model", None) or self.model,
            usage=getattr(response, "usage", None),
            warnings=[str(item) for item in warnings if item is not None],
            provider="deepseek",
            task=request.task,
            raw=response,
        )

    def _fallback_response(self, request: ModelRequest) -> ModelGatewayResponse:
        if request.response_format == "json":
            text = json.dumps(request.context, ensure_ascii=False)
        else:
            text = (
                "ModelGateway deterministic fallback. "
                f"Context: {json.dumps(request.context, ensure_ascii=False)}"
            )
        return ModelGatewayResponse(
            text=text,
            model=self.model,
            usage=None,
            warnings=["ModelGateway provider unavailable; deterministic fallback used."],
            provider="fallback",
            task=request.task,
        )

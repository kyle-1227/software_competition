from __future__ import annotations

from app.core.config import settings
from app.services.tools.manual_lookup import ManualLookupTool


def test_manual_lookup_builds_reranker_when_enabled_and_api_key_present(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "reranker_enabled", True)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")

    tool = ManualLookupTool()

    assert tool.retriever._reranker is not None


def test_manual_lookup_does_not_build_reranker_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "reranker_enabled", True)
    monkeypatch.setattr(settings, "siliconflow_api_key", None)

    tool = ManualLookupTool()

    assert tool.retriever._reranker is None


def test_manual_lookup_builds_query_rewriter_when_hyde_enabled_and_llm_present(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "hyde_enabled", True)

    tool = ManualLookupTool(llm_client=object())

    assert tool.retriever._query_rewriter is not None

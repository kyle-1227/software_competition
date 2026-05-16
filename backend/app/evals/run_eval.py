from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from app.evals.metrics import build_comparison_report, compute_metrics, load_cases
from app.services.tracing.summary import build_trace_summary

DATASET_PATH = Path(__file__).parent / "datasets" / "manual_qa_20.jsonl"


async def run_eval(mode: str, provider: str, dataset: Path) -> dict[str, Any]:
    if provider != "hash" and os.getenv("RUN_LIVE_EMBEDDING_TESTS") != "1":
        raise RuntimeError(
            "Live embedding eval requires RUN_LIVE_EMBEDDING_TESTS=1; use --provider hash for offline eval."
        )

    cases = load_cases(dataset)
    results = []
    if mode == "retriever":
        runner = _build_retriever_runner(provider)
    elif mode == "harness":
        runner = _build_harness_runner(provider)
    else:
        raise ValueError(f"Unsupported eval mode: {mode}")

    for case in cases:
        started = time.perf_counter()
        result = await runner(case)
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        results.append(result)

    return {
        "mode": mode,
        "provider": provider,
        "dataset": str(dataset),
        "cases": results,
        "metrics": compute_metrics(results),
    }


def _build_retriever_runner(provider: str):
    from app.services.manual_vector_indexer import ManualHashEmbedding
    from app.services.retriever import Retriever

    embed_model = ManualHashEmbedding() if provider == "hash" else None
    retriever = Retriever(embed_model=embed_model)

    async def _run(case: dict[str, Any]) -> dict[str, Any]:
        evidence = await retriever.search(
            case["question"],
            case.get("device_name"),
        )
        evidence_dicts = [item.model_dump(mode="json") for item in evidence]
        answer = "\n".join(item.get("snippet", "") for item in evidence_dicts)
        return _case_result(case, evidence_dicts, answer, None)

    return _run


def _build_harness_runner(provider: str):
    from app.schemas.query import QueryRequest
    from app.services.agent_harness_lc import AgentHarness

    harness = _offline_hash_harness() if provider == "hash" else AgentHarness()

    async def _run(case: dict[str, Any]) -> dict[str, Any]:
        response = await harness.answer(
            QueryRequest(
                question=case["question"],
                device_name=case.get("device_name"),
                session_id=f"eval-{case['id']}",
            )
        )
        evidence = [item.model_dump(mode="json") for item in response.evidence]
        evaluation = (
            response.evaluation.model_dump(mode="json")
            if response.evaluation is not None
            else None
        )
        trace_id = response.trace_id
        trace = _get_trace_tree(harness.trace_store, trace_id)
        return _case_result(
            case,
            evidence,
            response.answer,
            evaluation,
            trace_id=trace_id,
            trace_summary=_trace_summary(harness.trace_store, trace_id),
            trace_usage_summary=build_trace_summary(trace),
        )

    return _run


def _offline_hash_harness():
    from app.services.agent_harness_lc import AgentHarness
    from app.services.evaluator import Evaluator
    from app.services.manual_vector_indexer import ManualHashEmbedding
    from app.services.memory_store import MemoryStore
    from app.services.retriever import Retriever
    from app.services.sandbox import SandboxExecutor
    from app.services.tool_registry import ToolRegistry
    from app.services.tools.ai_coding import AICodingTool
    from app.services.tools.compliance_check import ComplianceCheckTool
    from app.services.tools.manual_lookup import ManualLookupTool
    from app.services.trace_store import TraceStore

    registry = ToolRegistry(register_defaults=False)
    registry.register(
        ManualLookupTool(retriever=Retriever(embed_model=ManualHashEmbedding()))
    )
    registry.register(AICodingTool(llm_client=None))
    registry.register(ComplianceCheckTool())
    return AgentHarness(
        tool_registry=registry,
        trace_store=TraceStore(),
        memory_store=MemoryStore(),
        sandbox_executor=SandboxExecutor(),
        evaluator=Evaluator(),
        llm_client=None,
    )


def _case_result(
    case: dict[str, Any],
    evidence: list[dict[str, Any]],
    answer: str,
    evaluation: dict[str, Any] | None,
    *,
    trace_id: str | None = None,
    trace_summary: dict[str, Any] | None = None,
    trace_usage_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    retrieved_pages = [
        item.get("page")
        for item in evidence
        if item.get("page") is not None
    ]
    haystack = answer + "\n" + "\n".join(str(item.get("snippet", "")) for item in evidence)
    expected_terms = [str(term) for term in case.get("expected_terms", [])]
    matched_terms = [term for term in expected_terms if term and term in haystack]
    placeholder_used = any(
        isinstance(item.get("metadata"), dict)
        and item["metadata"].get("retriever") == "llama-index-placeholder"
        for item in evidence
    )
    trace_fields = trace_summary or _empty_trace_summary()
    return {
        "id": case["id"],
        "question": case["question"],
        "expected_pages": case.get("expected_pages", []),
        "retrieved_pages": retrieved_pages,
        "expected_terms": expected_terms,
        "matched_terms": matched_terms,
        "evidence_count": len(evidence),
        "placeholder_used": placeholder_used,
        "answer": answer,
        "evaluation": evaluation,
        "latency_ms": 0,
        "trace_id": trace_id,
        "trace_summary": trace_usage_summary or build_trace_summary(None),
        **trace_fields,
    }


def _trace_summary(trace_store: Any, trace_id: str | None) -> dict[str, Any]:
    if not trace_id:
        return _empty_trace_summary()
    trace = _get_trace_tree(trace_store, trace_id)
    if trace is None:
        return _empty_trace_summary()
    spans = list(_walk_spans(trace.root_span))
    observed_spans = [span for span in spans if span is not trace.root_span]
    return {
        "trace_span_count": len(observed_spans),
        "trace_error_count": sum(
            1
            for span in observed_spans
            if getattr(span.status, "value", span.status) == "error"
        ),
        "trace_has_degraded_tool": any(
            span.name.startswith("tool.") and bool(span.metadata.get("degraded"))
            for span in observed_spans
        ),
        "trace_has_retrieval_retry": any(
            span.name == "node.retrieval_retry" for span in observed_spans
        ),
        "trace_has_local_llm_fallback": any(
            span.name == "llm.answer_generation"
            and (
                bool(span.metadata.get("local_fallback"))
                or bool(span.metadata.get("fallback_used"))
            )
            for span in observed_spans
        ),
        "trace_has_approval": any(
            span.name == "node.approval"
            or bool(span.metadata.get("requires_human_approval"))
            for span in observed_spans
        ),
        "trace_has_fail_safe": any(
            span.name == "node.fail_safe" for span in observed_spans
        ),
    }


def _empty_trace_summary() -> dict[str, Any]:
    return {
        "trace_span_count": 0,
        "trace_error_count": 0,
        "trace_has_degraded_tool": False,
        "trace_has_retrieval_retry": False,
        "trace_has_local_llm_fallback": False,
        "trace_has_approval": False,
        "trace_has_fail_safe": False,
    }


def _get_trace_tree(trace_store: Any, trace_id: str | None) -> Any:
    if not trace_id:
        return None
    get_trace_tree = getattr(trace_store, "get_trace_tree", None)
    if not callable(get_trace_tree):
        return None
    return get_trace_tree(trace_id)


def _walk_spans(span: Any):
    yield span
    for child in getattr(span, "children", []) or []:
        yield from _walk_spans(child)


def _load_eval_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline Agent Harness evals.")
    parser.add_argument("--mode", choices=["retriever", "harness"])
    parser.add_argument("--provider", default="hash")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", nargs=2, type=Path)
    args = parser.parse_args(argv)

    if args.compare:
        old_payload = _load_eval_payload(args.compare[0])
        new_payload = _load_eval_payload(args.compare[1])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            build_comparison_report(old_payload, new_payload),
            encoding="utf-8",
        )
        return 0

    if not args.mode:
        parser.error("--mode is required unless --compare is used")

    import asyncio

    payload = asyncio.run(run_eval(args.mode, args.provider, args.dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
